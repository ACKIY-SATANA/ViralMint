import React from "react"
import ReactDOM from "react-dom/client"
import { CssBaseline } from "@mui/material"
import App from "./App"
import ThemeWrapper from "./ThemeWrapper"
import { installTranslationGuard } from "./utils/translationGuard"

// Self-hosted Inter — replaces the previous fonts.googleapis.com link.
// Same files Google would have served, vendored through @fontsource so
// no third party sees a request for every page load. Weights match
// what theme.js asks for (300/400/500/600/700).
import "@fontsource/inter/300.css"
import "@fontsource/inter/400.css"
import "@fontsource/inter/500.css"
import "@fontsource/inter/600.css"
import "@fontsource/inter/700.css"

// Before the first render: browser page-translation re-parents the text nodes
// React tracks, which otherwise crashes the app into the ErrorBoundary.
installTranslationGuard()

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ThemeWrapper>
      <CssBaseline />
      <App />
    </ThemeWrapper>
  </React.StrictMode>
)
