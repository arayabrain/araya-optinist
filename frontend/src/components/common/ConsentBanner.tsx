import { FC, useEffect, useState } from "react"
import { useSelector } from "react-redux"

import Box from "@mui/material/Box"
import Button from "@mui/material/Button"
import Link from "@mui/material/Link"
import Paper from "@mui/material/Paper"
import Typography from "@mui/material/Typography"

import { Z_INDEX } from "const/Layout"
import { FONT_SIZE, TEXT_COLOR } from "const/Style"
import { selectModeStandalone } from "store/slice/Standalone/StandaloneSeclector"
import {
  getAnalyticsConsent,
  isGtmEnabled,
  setAnalyticsConsent,
  subscribeAnalyticsConsent,
} from "utils/analytics"

const REOPEN_EVENT = "consent-banner-reopen"

// Leaves the stored choice in force until the visitor answers the reopened notice.
export const CookieSettingsLink: FC = () => {
  if (!isGtmEnabled()) return null
  return (
    <Link
      component="button"
      type="button"
      onClick={() => window.dispatchEvent(new Event(REOPEN_EVENT))}
      sx={{
        fontFamily: "inherit",
        fontSize: FONT_SIZE.SMALL,
        color: TEXT_COLOR.SECONDARY,
        textDecorationColor: "currentColor",
        transition: "color 0.2s",
        "&:hover": { color: TEXT_COLOR.ACCENT },
      }}
    >
      Cookie settings
    </Link>
  )
}

const ConsentBanner: FC = () => {
  const isStandalone = useSelector(selectModeStandalone)
  const [decided, setDecided] = useState(() => getAnalyticsConsent() !== null)

  // Any decision closes the notice, including one made on /account while reopened.
  useEffect(() => {
    const reopen = () => setDecided(false)
    window.addEventListener(REOPEN_EVENT, reopen)
    const unsubscribe = subscribeAnalyticsConsent(() => setDecided(true))
    return () => {
      window.removeEventListener(REOPEN_EVENT, reopen)
      unsubscribe()
    }
  }, [])

  // Only once the backend has confirmed hosted mode: standalone is a local
  // install, and an unresolved mode means the app itself is not usable yet.
  if (!isGtmEnabled() || isStandalone !== false || decided) return null

  return (
    <Paper
      elevation={8}
      role="region"
      aria-label="Analytics cookie consent"
      aria-live="polite"
      data-testid="consent-banner"
      sx={{
        position: "fixed",
        // Bottom-right, one line tall: keeps the notice clear of the fixed
        // controls and toasts that sit in the bottom-left corner.
        bottom: 16,
        right: 16,
        maxWidth: "calc(100vw - 32px)",
        zIndex: Z_INDEX.CONSENT_BANNER,
        px: 2,
        py: 1.5,
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 2,
      }}
    >
      <Typography variant="body2">
        We use Google Analytics cookies to measure site usage; Google processes
        this data in the United States. See our{" "}
        {/* Outside the router, so a plain link; a new tab keeps the notice. */}
        <Link
          href="/privacy"
          target="_blank"
          rel="noopener"
          data-testid="consent-privacy-link"
        >
          Privacy Policy
        </Link>
        .
      </Typography>
      <Box sx={{ display: "flex", gap: 1, ml: "auto" }}>
        {/* Identical styling on both: a de-emphasised Decline is a dark pattern. */}
        <Button
          size="small"
          variant="outlined"
          onClick={() => setAnalyticsConsent("denied")}
          data-testid="consent-decline"
        >
          Decline
        </Button>
        <Button
          size="small"
          variant="outlined"
          onClick={() => setAnalyticsConsent("granted")}
          data-testid="consent-accept"
        >
          Accept
        </Button>
      </Box>
    </Paper>
  )
}

export default ConsentBanner
