import React from "react"

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

// eslint-disable-next-line no-relative-import-paths/no-relative-import-paths
import "./LandingPage.css"

export const LandingPage: React.FC = () => {
  return (
    <div className="landing-page">
      <Header />
      <main className="landing-main">
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
      </main>
      <Footer />
    </div>
  )
}

export default LandingPage
