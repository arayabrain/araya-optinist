import { FC, ReactNode } from "react"
import { useLocation, useNavigate } from "react-router-dom"

import ArrowBackIcon from "@mui/icons-material/ArrowBack"
import { Box, Button, styled, Typography } from "@mui/material"

import PublicHeader from "components/PublicLayout/PublicHeader"

type LegalPageLayoutProps = {
  title: string
  lastUpdated: string
  children: ReactNode
}

const LegalPageLayout: FC<LegalPageLayoutProps> = ({
  title,
  lastUpdated,
  children,
}) => {
  const navigate = useNavigate()
  const location = useLocation()

  // key is "default" on the router's first entry, i.e. the page was opened in a
  // new tab and there is nothing to go back to
  const goBack = () =>
    location.key === "default" ? navigate("/") : navigate(-1)

  return (
    <>
      <PublicHeader />
      <Wrapper>
        <Button
          size="small"
          startIcon={<ArrowBackIcon />}
          onClick={goBack}
          sx={{ mb: 2, color: "inherit", textTransform: "none" }}
        >
          Back
        </Button>
        <Typography variant="h4" component="h1" sx={{ mb: 1 }}>
          {title}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
          Last updated: {lastUpdated}
        </Typography>
        {children}
      </Wrapper>
    </>
  )
}

const Wrapper = styled(Box)({
  maxWidth: 800,
  margin: "0 auto",
  padding: "96px 24px 48px",
  // Long URLs and table cells would otherwise force horizontal scrolling on narrow screens
  wordBreak: "break-word",
})

export default LegalPageLayout
