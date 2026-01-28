import { Box, styled } from "@mui/material"

import {
  Header,
  Hero,
  ValueProps,
  Features,
  FormatMarquee,
  HowItWorks,
  Audience,
  Collaboration,
  PublicRepository,
  Resources,
  CTA,
  Footer,
} from "pages/LandingPage/components"

export const LandingPage = () => {
  return (
    <LandingPageWrapper>
      <Header />
      <Main>
        <Hero />
        <ValueProps />
        <Features />
        <FormatMarquee />
        <HowItWorks />
        <Audience />
        <Collaboration />
        <PublicRepository />
        <Resources />
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
