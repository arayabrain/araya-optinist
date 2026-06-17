import { useEffect } from "react"

import { Box, styled } from "@mui/material"

import {
  Header,
  Hero,
  ValueProps,
  Features,
  FormatMarquee,
  Audience,
  PublicRepository,
  Pricing,
  Publications,
  CTA,
  Footer,
} from "pages/LandingPage/components"

export const LandingPage = () => {
  useEffect(() => {
    // Enable smooth scrolling for anchor links
    document.documentElement.style.scrollBehavior = "smooth"

    return () => {
      // Reset when leaving the page
      document.documentElement.style.scrollBehavior = "auto"
    }
  }, [])

  return (
    <LandingPageWrapper>
      <Header />
      <Main>
        <Hero />
        <ValueProps />
        <Features />
        <FormatMarquee />
        <Audience />
        <PublicRepository />
        <Pricing />
        <Publications />
        <CTA />
      </Main>
      <Footer />
    </LandingPageWrapper>
  )
}

const LandingPageWrapper = styled(Box)({
  color: "#111827",
  backgroundColor: "#f9fafb",
  minHeight: "100vh",
  "& *": {
    boxSizing: "border-box",
  },
  "& .material-symbols-outlined": {
    fontFamily: "Material Symbols Outlined",
  },
})

const Main = styled("main")({
  display: "flex",
  flexDirection: "column",
})

export default LandingPage
