# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
Post-upload performance tracking service.
Periodically polls YouTube/TikTok APIs for video metrics after upload.

Polling schedule:
- First 24h: every 2 hours
- Days 2-7: every 6 hours
- After 7 days: daily
- After 30 days: stop polling
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select, and_

from backend.database import AsyncSessionLocal
from backend.models.generated_video import GeneratedVideo
from backend.models.video_metrics import VideoMetrics

logger = logging.getLogger(__name__)


async def poll_all_uploaded_videos():
    """
    Refresh metrics for all uploaded videos. Can be invoked on demand
    (e.g. from an admin API endpoint or a manual maintenance script).
    """
    async with AsyncSessionLocal() as db:
        # Find videos uploaded in the last 30 days
        cutoff = datetime.utcnow() - timedelta(days=30)
        result = await db.execute(
            select(GeneratedVideo).where(
                and_(
                    GeneratedVideo.status == "uploaded",
                    GeneratedVideo.created_at >= cutoff,
                )
            )
        )
        videos = result.scalars().all()

    if not videos:
        return

    for video in videos:
        # Check if we should poll this video now
        if not _should_poll_now(video):
            continue

        platforms = json.loads(video.uploaded_platforms_json or "[]")

        for platform in platforms:
            try:
                if platform == "youtube" and video.youtube_video_id:
                    metrics = await fetch_youtube_metrics(video.youtube_video_id)
                    if metrics:
                        await _save_metrics(video.id, "youtube", metrics)
                elif platform == "tiktok" and video.tiktok_publish_id:
                    metrics = await fetch_tiktok_metrics(video.tiktok_publish_id)
                    if metrics:
                        await _save_metrics(video.id, "tiktok", metrics)
            except Exception as e:
                logger.warning(f"Failed to fetch {platform} metrics for {video.id}: {e}")

    logger.info(f"Performance tracking: polled {len(videos)} uploaded videos")


def _should_poll_now(video: GeneratedVideo) -> bool:
    """Determine if enough time has passed since the last poll for this video."""
    if not video.updated_at:
        return True

    age = datetime.utcnow() - video.created_at
    time_since_last_update = datetime.utcnow() - video.updated_at

    if age < timedelta(hours=24):
        # First 24h: every 2 hours
        return time_since_last_update >= timedelta(hours=2)
    elif age < timedelta(days=7):
        # Days 2-7: every 6 hours
        return time_since_last_update >= timedelta(hours=6)
    elif age < timedelta(days=30):
        # After 7 days: daily
        return time_since_last_update >= timedelta(days=1)
    else:
        # After 30 days: stop polling
        return False


async def fetch_youtube_metrics(youtube_video_id: str) -> dict | None:
    """Fetch video stats from YouTube Data API v3."""
    from backend.config import settings as env

    api_key = env.YOUTUBE_API_KEY
    if not api_key:
        return None

    def _fetch():
        import httpx
        resp = httpx.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "statistics,contentDetails",
                "id": youtube_video_id,
                "key": api_key,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"YouTube API error: {resp.status_code}")
            return None

        data = resp.json()
        items = data.get("items", [])
        if not items:
            return None

        stats = items[0].get("statistics", {})
        return {
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
            "shares": 0,  # YouTube API doesn't expose shares
        }

    return await asyncio.to_thread(_fetch)


async def fetch_tiktok_metrics(publish_id: str) -> dict | None:
    """
    Fetch video stats from TikTok API.
    Note: TikTok's API requires the access token, so this may not work
    for all users. Returns None if no token is available.
    """
    # Load user's TikTok access token
    from backend.core.crypto import decrypt_safe
    from backend.models.user_settings import UserSettings

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        settings = result.scalar_one_or_none()

    if not settings or not settings.tiktok_upload_token_encrypted:
        return None

    access_token = decrypt_safe(settings.tiktok_upload_token_encrypted)
    if not access_token:
        return None

    def _fetch():
        import httpx
        resp = httpx.post(
            "https://open.tiktokapis.com/v2/video/query/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={
                "filters": {"video_ids": [publish_id]},
                "fields": ["view_count", "like_count", "comment_count", "share_count"],
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        videos = data.get("data", {}).get("videos", [])
        if not videos:
            return None

        v = videos[0]
        return {
            "views": v.get("view_count", 0),
            "likes": v.get("like_count", 0),
            "comments": v.get("comment_count", 0),
            "shares": v.get("share_count", 0),
        }

    return await asyncio.to_thread(_fetch)


async def _save_metrics(generated_video_id: str, platform: str, metrics: dict):
    """Save a metrics snapshot to the video_metrics table."""
    async with AsyncSessionLocal() as db:
        record = VideoMetrics(
            generated_video_id=generated_video_id,
            platform=platform,
            views=metrics.get("views", 0),
            likes=metrics.get("likes", 0),
            comments=metrics.get("comments", 0),
            shares=metrics.get("shares", 0),
            watch_time_hours=metrics.get("watch_time_hours"),
            avg_view_duration=metrics.get("avg_view_duration"),
            ctr=metrics.get("ctr"),
        )
        db.add(record)
        await db.commit()


async def get_video_performance(generated_video_id: str) -> dict:
    """Get all metrics history for a video, grouped by platform."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(VideoMetrics)
            .where(VideoMetrics.generated_video_id == generated_video_id)
            .order_by(VideoMetrics.fetched_at.asc())
        )
        records = result.scalars().all()

    platforms = {}
    for r in records:
        if r.platform not in platforms:
            platforms[r.platform] = []
        platforms[r.platform].append({
            "views": r.views,
            "likes": r.likes,
            "comments": r.comments,
            "shares": r.shares,
            "watch_time_hours": r.watch_time_hours,
            "avg_view_duration": r.avg_view_duration,
            "ctr": r.ctr,
            "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
        })

    # Summary: latest metrics per platform
    latest = {}
    for platform, snapshots in platforms.items():
        if snapshots:
            latest[platform] = snapshots[-1]

    return {
        "generated_video_id": generated_video_id,
        "latest": latest,
        "history": platforms,
    }


async def get_performance_summary(user_id: str = "local") -> dict:
    """Get aggregate performance stats across all uploaded videos."""
    async with AsyncSessionLocal() as db:
        # Get all uploaded videos
        result = await db.execute(
            select(GeneratedVideo).where(
                and_(
                    GeneratedVideo.user_id == user_id,
                    GeneratedVideo.status == "uploaded",
                )
            )
        )
        videos = result.scalars().all()

        if not videos:
            return {"total_views": 0, "total_likes": 0, "total_videos": 0, "best_video": None}

        # Get latest metrics for each video
        total_views = 0
        total_likes = 0
        best_video = None
        best_views = 0

        for v in videos:
            metrics_result = await db.execute(
                select(VideoMetrics)
                .where(VideoMetrics.generated_video_id == v.id)
                .order_by(VideoMetrics.fetched_at.desc())
                .limit(1)
            )
            latest = metrics_result.scalar_one_or_none()
            if latest:
                total_views += latest.views
                total_likes += latest.likes
                if latest.views > best_views:
                    best_views = latest.views
                    best_video = {"id": v.id, "title": v.title, "views": latest.views}

    return {
        "total_views": total_views,
        "total_likes": total_likes,
        "total_videos": len(videos),
        "best_video": best_video,
    }


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday",
             "friday", "saturday", "sunday")


def _weekday_index(name: str) -> int:
    """Sort key for day names — Monday first, anything unrecognised last.

    Day keys come from `created_at.strftime("%A").lower()`, so a non-English
    locale can yield a name that isn't in the tuple; that must sort, not raise.
    """
    try:
        return _WEEKDAYS.index(name)
    except ValueError:
        return len(_WEEKDAYS)


async def recommend_posting_time(
    user_id: str = "local",
    platform: str = "youtube",
) -> dict:
    """
    Analyze when uploaded videos performed best and recommend optimal posting time.
    Groups videos by upload hour and day-of-week, compares early view velocity
    (views in first 24h) to find the best time slot.

    Requires at least 5 uploaded videos with performance data for meaningful results.
    """
    async with AsyncSessionLocal() as db:
        # Get all uploaded videos for this platform
        uploaded = await db.execute(
            select(GeneratedVideo).where(
                and_(
                    GeneratedVideo.user_id == user_id,
                    GeneratedVideo.status == "uploaded",
                )
            )
        )
        videos = uploaded.scalars().all()

        if not videos:
            return {
                "recommendation": None,
                "confidence": 0,
                "sample_size": 0,
                "message": "No uploaded videos found. Upload some videos first!",
            }

        # Filter to videos on the target platform
        platform_videos = []
        for v in videos:
            platforms = json.loads(v.uploaded_platforms_json or "[]")
            if platform in platforms:
                platform_videos.append(v)

        if len(platform_videos) < 3:
            return {
                "recommendation": None,
                "confidence": 0,
                "sample_size": len(platform_videos),
                "message": f"Need at least 3 videos uploaded to {platform} for recommendations (found {len(platform_videos)}).",
            }

        # For each video, get early metrics (first data point = ~2h after upload)
        hour_performance = {}   # hour (0-23) → list of early view counts
        day_performance = {}    # day name → list of early view counts

        for v in platform_videos:
            if not v.created_at:
                continue

            # Get the earliest metric snapshot for this video
            metrics_result = await db.execute(
                select(VideoMetrics)
                .where(
                    and_(
                        VideoMetrics.generated_video_id == v.id,
                        VideoMetrics.platform == platform,
                    )
                )
                .order_by(VideoMetrics.fetched_at.asc())
                .limit(1)
            )
            first_metric = metrics_result.scalar_one_or_none()
            if not first_metric or first_metric.views == 0:
                continue

            upload_hour = v.created_at.hour
            upload_day = v.created_at.strftime("%A").lower()  # monday, tuesday, etc.

            # Use views from first metric as proxy for early velocity
            hour_performance.setdefault(upload_hour, []).append(first_metric.views)
            day_performance.setdefault(upload_day, []).append(first_metric.views)

    if not hour_performance:
        return {
            "recommendation": None,
            "confidence": 0,
            "sample_size": len(platform_videos),
            "message": "Not enough performance data yet. Wait for metrics to be collected.",
        }

    # Find best hour
    hour_avgs = {h: sum(vs) / len(vs) for h, vs in hour_performance.items()}
    best_hour = max(hour_avgs, key=hour_avgs.get)
    overall_avg = sum(v for vs in hour_performance.values() for v in vs) / sum(len(vs) for vs in hour_performance.values())
    hour_lift = (hour_avgs[best_hour] / max(overall_avg, 1) - 1) * 100

    # Find best day
    best_day = None
    day_lift = 0
    # Initialised unconditionally: the response below reads day_avgs, and a
    # name that only exists inside the `if` is a NameError waiting for the next
    # edit.
    day_avgs: dict[str, float] = {}
    if day_performance:
        day_avgs = {d: sum(vs) / len(vs) for d, vs in day_performance.items()}
        best_day = max(day_avgs, key=day_avgs.get)
        day_avg_overall = sum(v for vs in day_performance.values() for v in vs) / sum(len(vs) for vs in day_performance.values())
        day_lift = (day_avgs[best_day] / max(day_avg_overall, 1) - 1) * 100

    # Confidence based on sample size
    total_data_points = sum(len(vs) for vs in hour_performance.values())
    if total_data_points >= 20:
        confidence = 0.85
    elif total_data_points >= 10:
        confidence = 0.65
    elif total_data_points >= 5:
        confidence = 0.45
    else:
        confidence = 0.25

    note_parts = [f"Based on {total_data_points} uploads"]
    if hour_lift > 10:
        note_parts.append(f"posting at {best_hour}:00 UTC gets {hour_lift:.0f}% more early views")
    if best_day and day_lift > 10:
        note_parts.append(f"{best_day.title()}s perform {day_lift:.0f}% better")

    return {
        "recommendation": {
            "hour_utc": best_hour,
            "day": best_day,
            "platform": platform,
        },
        "confidence": round(confidence, 2),
        "sample_size": total_data_points,
        "hour_lift_pct": round(hour_lift, 1),
        "day_lift_pct": round(day_lift, 1),
        "hour_breakdown": {str(h): round(avg, 1) for h, avg in sorted(hour_avgs.items())},
        # day_avgs, NOT day_performance: the latter maps day → LIST of view
        # counts, and round() on a list raises TypeError — which took out this
        # whole endpoint on its happy path (any user with upload history).
        "day_breakdown": {
            d: round(avg, 1)
            for d, avg in sorted(day_avgs.items(), key=lambda kv: _weekday_index(kv[0]))
        },
        "message": ". ".join(note_parts) + ".",
    }
