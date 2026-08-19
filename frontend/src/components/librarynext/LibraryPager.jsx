/**
 * LibraryPager — the Library's page control.
 *
 * Pages REPLACE rather than append. The first cut used "Load more", and on a real
 * library that measured 240 tiles → 5,228 DOM nodes and 403 <img> elements after
 * three clicks, growing without limit; swapping the page keeps the cost flat
 * however deep you go.
 *
 * Page state lives in the URL (`?page=`, `?per=`) alongside the filters, so a
 * link carries the exact view — and any filter change resets to page 1, because
 * page 7 of an old result set is a guaranteed empty grid that reads as a bug.
 */
import {
  Box, Stack, Typography, Pagination, Select, MenuItem, useMediaQuery, useTheme,
} from "@mui/material"
import { PAGE_SIZES } from "../../hooks/useLibraryIndex"

export default function LibraryPager({
  page, pageCount, pageSize, onPage, onPageSize,
  shown, total, libraryTotal, unit = "items",
  filtersActive, onClearFilters,
}) {
  const theme = useTheme()
  const narrow = useMediaQuery(theme.breakpoints.down("md"))
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1
  const last = (page - 1) * pageSize + shown

  return (
    <Stack alignItems="center" spacing={1.25} sx={{ mt: 3, pb: 1 }}>
      {pageCount > 1 && (
        <Pagination
          count={pageCount}
          page={Math.min(page, pageCount)}
          onChange={(_, p) => onPage(p)}
          size="small"
          shape="rounded"
          // On a narrow window the ellipsis form still fits; siblings=0 keeps it
          // to first / current / last rather than wrapping onto two lines.
          siblingCount={narrow ? 0 : 1}
          boundaryCount={1}
          sx={{ "& .MuiPaginationItem-root": { fontWeight: 600 } }}
        />
      )}

      <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap"
        justifyContent="center">
        <Typography sx={{ fontSize: "0.75rem", color: "text.disabled" }}>
          {total === 0
            ? `No ${unit}`
            : `${first}–${last} of ${total} ${unit}`}
          {filtersActive && libraryTotal ? ` (of ${libraryTotal} in your library)` : ""}
        </Typography>

        {filtersActive && (
          <Typography component="span" onClick={onClearFilters}
            sx={{
              fontSize: "0.75rem", color: "primary.main", cursor: "pointer",
              fontWeight: 600,
            }}>
            clear filters
          </Typography>
        )}

        {total > PAGE_SIZES[0] && (
          <Stack direction="row" spacing={0.75} alignItems="center">
            <Typography sx={{ fontSize: "0.75rem", color: "text.disabled" }}>
              per page
            </Typography>
            <Select
              size="small" value={pageSize}
              onChange={(e) => onPageSize(Number(e.target.value))}
              aria-label="Items per page"
              sx={{
                height: 26, fontSize: "0.75rem",
                "& .MuiSelect-select": { py: 0, pl: 1 },
              }}
            >
              {PAGE_SIZES.map((n) => (
                <MenuItem key={n} value={n} sx={{ fontSize: "0.8rem" }}>{n}</MenuItem>
              ))}
            </Select>
          </Stack>
        )}
      </Stack>
    </Stack>
  )
}
