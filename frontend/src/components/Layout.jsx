import { useState } from "react"
import { Outlet, NavLink, useLocation } from "react-router-dom"
import useWebSocket from "../hooks/useWebSocket"
import {
  Box, Drawer, List, ListItemButton, ListItemIcon, ListItemText,
  Typography, Divider, IconButton, useMediaQuery, useTheme, Tooltip, Badge,
} from "@mui/material"
import useAppStore from "../store/appStore"
import { pluginNavItems } from "../plugins"
import MenuIcon from "@mui/icons-material/MenuOutlined"
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft"
import ChevronRightIcon from "@mui/icons-material/ChevronRight"
import ChatIcon from "@mui/icons-material/ChatBubbleOutline"
import VideoLibraryIcon from "@mui/icons-material/OndemandVideoOutlined"
import PhotoLibraryIcon from "@mui/icons-material/PhotoLibraryOutlined"
import SensorsIcon from "@mui/icons-material/SensorsOutlined"
import PhoneIphoneIcon from "@mui/icons-material/PhoneIphoneOutlined"
import ContentCutIcon from "@mui/icons-material/ContentCutOutlined"
import MovieFilterIcon from "@mui/icons-material/MovieFilterOutlined"
import BuildIcon from "@mui/icons-material/BuildOutlined"
import SettingsIcon from "@mui/icons-material/SettingsOutlined"

const DRAWER_WIDTH = 240
const COLLAPSED_WIDTH = 64

const navItems = [
  { to: "/",          icon: <ChatIcon />,             label: "Chat" },
  { to: "/channels",  icon: <SensorsIcon />,          label: "My Channels" },
  { to: "/clips",     icon: <ContentCutIcon />,       label: "Clip Studio" },
  { to: "/videos",    icon: <VideoLibraryIcon />,     label: "Library" },
  { to: "/stock",     icon: <PhotoLibraryIcon />,     label: "Stock Video" },
  { to: "/motion",    icon: <MovieFilterIcon />,      label: "Motion Graphics" },
  { to: "/tools",     icon: <BuildIcon />,            label: "Tools" },
  { to: "/messaging", icon: <PhoneIphoneIcon />,      label: "Messaging" },
  ...pluginNavItems.filter(i => (i.position || "top") === "top"),
]

const bottomItems = [
  ...pluginNavItems.filter(i => i.position === "bottom"),
  { to: "/settings",  icon: <SettingsIcon />,     label: "Settings" },
]

export default function Layout() {
  useWebSocket()  // Global WS connection — active on all pages
  const location = useLocation()
  const theme = useTheme()
  const isNarrow = useMediaQuery(theme.breakpoints.down("md"))
  const [mobileOpen, setMobileOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const activeJobs = useAppStore(s => s.activeJobs)
  const runningJobCount = Object.values(activeJobs).filter(j => j.status === "running").length

  const drawerWidth = collapsed && !isNarrow ? COLLAPSED_WIDTH : DRAWER_WIDTH

  const isActive = (to) => {
    if (to === "/") return location.pathname === "/"
    return location.pathname.startsWith(to)
  }

  const renderNavItem = ({ to, icon, label }) => {
    const active = isActive(to)
    const isCollapsed = collapsed && !isNarrow
    const renderedIcon = (to === "/videos" && runningJobCount > 0)
      ? <Badge color="warning" variant="dot">{icon}</Badge>
      : icon
    const button = (
      <ListItemButton
        key={to}
        component={NavLink}
        to={to}
        end={to === "/"}
        selected={active}
        sx={{
          borderRadius: 2.5,
          mb: 0.5,
          py: 0.85,
          px: isCollapsed ? 0 : 1.5,
          justifyContent: isCollapsed ? "center" : "flex-start",
          position: "relative",
          color: active ? "primary.main" : "text.secondary",
          "&.Mui-selected": {
            bgcolor: "rgba(201,100,66,0.1)",
            boxShadow: (theme) => `inset 0 0 0 1px rgba(201,100,66,0.12), ${theme.customShadows?.sm}`,
            "&:hover": { bgcolor: "rgba(201,100,66,0.13)" },
          },
          "&:hover": {
            bgcolor: "action.hover",
            color: "text.primary",
            "& .nav-icon": { transform: "scale(1.1)" },
          },
          transition: "all 0.15s ease",
        }}
      >
        <ListItemIcon
          className="nav-icon"
          sx={{
            minWidth: isCollapsed ? 0 : 34,
            color: "inherit",
            fontSize: 20,
            transition: "transform 0.15s ease",
          }}
        >
          {renderedIcon}
        </ListItemIcon>
        {!isCollapsed && (
          <ListItemText
            primary={label}
            primaryTypographyProps={{
              fontSize: "0.875rem",
              fontWeight: active ? 700 : 500,
              letterSpacing: "-0.01em",
            }}
          />
        )}
      </ListItemButton>
    )

    if (collapsed && !isNarrow) {
      return <Tooltip key={to} title={label} placement="right" arrow>{button}</Tooltip>
    }
    return button
  }

  const drawerContent = (
    <>
      {/* Logo + collapse toggle */}
      <Box sx={{ px: collapsed && !isNarrow ? 1 : 2.5, py: 2.5, display: "flex", alignItems: "center", justifyContent: collapsed && !isNarrow ? "center" : "space-between" }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.2, overflow: "hidden" }}>
          <Box
            component="img"
            src="/icon-192.png"
            alt="ViralMint"
            sx={{ width: 32, height: 32, borderRadius: 1, flexShrink: 0 }}
          />
          {(!collapsed || isNarrow) && (
            <Typography
              variant="h6"
              sx={{
                fontWeight: 700,
                letterSpacing: -0.5,
                fontSize: "1.15rem",
                background: "linear-gradient(135deg, #0D9F6E, #34D399)",
                WebkitBackgroundClip: "text",
                WebkitTextFillColor: "transparent",
                whiteSpace: "nowrap",
              }}
            >
              ViralMint
            </Typography>
          )}
        </Box>
        {!isNarrow && !collapsed && (
          <IconButton size="small" onClick={() => setCollapsed(true)} sx={{
            ml: 0.5,
            color: "primary.main",
            bgcolor: "action.hover",
            border: 1,
            borderColor: "divider",
            "&:hover": { bgcolor: "primary.main", color: "#fff" },
            transition: "all 0.15s",
          }}>
            <ChevronLeftIcon sx={{ fontSize: 18 }} />
          </IconButton>
        )}
      </Box>

      <Divider sx={{ mx: collapsed && !isNarrow ? 1 : 2, mb: 1, opacity: 0.5 }} />

      <List sx={{ px: collapsed && !isNarrow ? 0.75 : 1.5, flex: 1 }}>
        {navItems.map(renderNavItem)}
      </List>

      <Divider sx={{ mx: collapsed && !isNarrow ? 1 : 2, mb: 0.5, opacity: 0.5 }} />

      <List sx={{ px: collapsed && !isNarrow ? 0.75 : 1.5, pb: 1 }}>
        {bottomItems.map(renderNavItem)}
        {/* Expand button at the bottom when collapsed */}
        {!isNarrow && collapsed && (
          <Tooltip title="Expand sidebar" placement="right" arrow>
            <ListItemButton
              onClick={() => setCollapsed(false)}
              sx={{
                borderRadius: 2, py: 0.75, justifyContent: "center",
                border: 1, borderColor: "divider",
                color: "primary.main",
                "&:hover": { bgcolor: "primary.main", color: "#fff" },
                transition: "all 0.15s",
              }}
            >
              <ChevronRightIcon sx={{ fontSize: 20 }} />
            </ListItemButton>
          </Tooltip>
        )}
      </List>
    </>
  )

  return (
    <Box sx={{ display: "flex", height: "100vh" }}>
      {/* Mobile: overlay drawer */}
      {isNarrow ? (
        <Drawer
          variant="temporary"
          open={mobileOpen}
          onClose={() => setMobileOpen(false)}
          ModalProps={{ keepMounted: true }}
          sx={{
            "& .MuiDrawer-paper": { width: DRAWER_WIDTH },
          }}
        >
          {drawerContent}
        </Drawer>
      ) : (
        <Drawer
          variant="permanent"
          sx={{
            width: drawerWidth,
            flexShrink: 0,
            transition: "width 0.2s ease",
            "& .MuiDrawer-paper": {
              width: drawerWidth,
              transition: "width 0.2s ease",
              overflowX: "hidden",
            },
          }}
        >
          {drawerContent}
        </Drawer>
      )}

      <Box
        component="main"
        sx={{
          flex: 1,
          overflow: "auto",
          bgcolor: "background.default",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* Mobile top bar with hamburger */}
        {isNarrow && (
          <Box sx={{
            display: "flex", alignItems: "center", gap: 1,
            px: 1.5, py: 1, flexShrink: 0,
            borderBottom: 1, borderColor: "divider",
            bgcolor: "background.paper",
          }}>
            <IconButton size="small" onClick={() => setMobileOpen(true)}>
              <MenuIcon />
            </IconButton>
            <Box component="img" src="/icon-192.png" alt="" sx={{ width: 24, height: 24, borderRadius: 0.5 }} />
            <Typography
              sx={{
                fontWeight: 700, fontSize: "0.95rem",
                background: "linear-gradient(135deg, #0D9F6E, #34D399)",
                WebkitBackgroundClip: "text",
                WebkitTextFillColor: "transparent",
              }}
            >
              ViralMint
            </Typography>
          </Box>
        )}
        <Box sx={{ flex: 1, overflow: "auto" }}>
          <Outlet />
        </Box>
      </Box>
    </Box>
  )
}
