import { useNavigate } from "react-router-dom"

import { Box, styled, Typography } from "@mui/material"

import {
  FONT_SIZE,
  FONT_WEIGHT,
  LINE_HEIGHT,
  LETTER_SPACING,
  TEXT_COLOR,
  ACCENT_COLOR,
  BG_COLOR,
  BORDER_COLOR,
  BUTTON_BASE,
} from "pages/LandingPage/constants"

// Free-plan defaults are documented in docs/for_users/sign_up.md and
// docs/other/plan_expiration.md (5 GB storage, full workflow features,
// no credit card required).
const freePlanFeatures = [
  "Full no-code workflow builder",
  "5 GB cloud storage",
  "NWB import / export",
  "Public Repository access",
  "Shareable workspaces",
  "No credit card required",
]

// Premium positioning intentionally kept light; details live on
// the Subscription page.
const premiumHighlights = [
  "Expanded storage",
  "Premium compute instances",
  "Priority support",
]

export const Pricing = () => {
  const navigate = useNavigate()

  return (
    <PricingSection id="pricing">
      <Container>
        <SectionHeader>
          <SectionLabel>
            <span
              className="material-symbols-outlined"
              style={{ fontSize: FONT_SIZE.CARD_TITLE }}
            >
              sell
            </span>
            Pricing
          </SectionLabel>
          <SectionTitle>Start for Free</SectionTitle>
          <SectionSubtitle>
            Get the full OptiNiSt experience at no cost. Upgrade to Premium only
            when your lab needs more storage or compute.
          </SectionSubtitle>
        </SectionHeader>

        <FreeCard>
          <FreeBadge>Free Trial</FreeBadge>
          <FreeTitle>Free</FreeTitle>
          <FreePriceRow>
            <FreePrice>$0</FreePrice>
            <FreePriceCaption>forever</FreePriceCaption>
          </FreePriceRow>
          <FreeTagline>
            Everything you need to go from raw data to publishable insights
            &mdash; no credit card, no time limit.
          </FreeTagline>
          <FreeFeatureGrid>
            {freePlanFeatures.map((feature, index) => (
              <FreeFeature key={index}>
                <span className="material-symbols-outlined">check_circle</span>
                <span>{feature}</span>
              </FreeFeature>
            ))}
          </FreeFeatureGrid>
          <FreeCTA onClick={() => navigate("/register")}>
            Get Started Free
          </FreeCTA>
          <FreeFootnote>
            Free plan includes 5 GB storage and the full workflow builder.
          </FreeFootnote>
        </FreeCard>

        <PremiumNote>
          Need more?{" "}
          <PremiumStrong>Premium</PremiumStrong> adds{" "}
          {premiumHighlights.join(", ")}.{" "}
          <PremiumLink onClick={() => navigate("/subscription")}>
            Compare plans &rarr;
          </PremiumLink>
        </PremiumNote>
      </Container>
    </PricingSection>
  )
}

const PricingSection = styled("section")({
  backgroundColor: BG_COLOR.PAGE,
  padding: "5rem 0",
})

const Container = styled(Box)({
  maxWidth: 1200,
  margin: "0 auto",
  padding: "0 1.5rem",
})

const SectionHeader = styled(Box)({
  display: "flex",
  flexDirection: "column",
  textAlign: "center",
  alignItems: "center",
  marginBottom: "3rem",
})

const SectionLabel = styled(Box)({
  display: "inline-flex",
  alignItems: "center",
  gap: "0.5rem",
  backgroundColor: ACCENT_COLOR.GREEN.bg,
  color: ACCENT_COLOR.GREEN.color,
  fontWeight: FONT_WEIGHT.BOLD,
  fontSize: FONT_SIZE.SMALL,
  padding: "0.5rem 1rem",
  borderRadius: 9999,
  width: "fit-content",
})

const SectionTitle = styled(Typography)({
  fontSize: FONT_SIZE.SECTION_TITLE,
  fontWeight: FONT_WEIGHT.BOLD,
  margin: "1rem 0 1rem",
})

const SectionSubtitle = styled(Typography)({
  color: TEXT_COLOR.SECONDARY,
  fontSize: FONT_SIZE.SECTION_SUBTITLE,
  margin: 0,
  maxWidth: 620,
  lineHeight: LINE_HEIGHT.NORMAL,
})

const FreeCard = styled(Box)({
  position: "relative",
  maxWidth: 720,
  margin: "0 auto",
  backgroundColor: BG_COLOR.WHITE,
  borderRadius: 24,
  padding: "3rem 2.5rem",
  textAlign: "center",
  border: `2px solid ${ACCENT_COLOR.GREEN.color}`,
  boxShadow: "0 25px 50px -12px rgba(5, 150, 105, 0.15)",
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  gap: "1rem",
})

const FreeBadge = styled(Box)({
  position: "absolute",
  top: -16,
  backgroundColor: ACCENT_COLOR.GREEN.color,
  color: TEXT_COLOR.WHITE,
  fontWeight: FONT_WEIGHT.BOLD,
  fontSize: FONT_SIZE.SMALL,
  letterSpacing: LETTER_SPACING.WIDE,
  padding: "0.4rem 1rem",
  borderRadius: 9999,
  textTransform: "uppercase",
})

const FreeTitle = styled(Typography)({
  fontSize: FONT_SIZE.CARD_TITLE,
  fontWeight: FONT_WEIGHT.BOLD,
  color: TEXT_COLOR.SECONDARY,
  letterSpacing: LETTER_SPACING.WIDE,
  textTransform: "uppercase",
  margin: 0,
})

const FreePriceRow = styled(Box)({
  display: "flex",
  alignItems: "baseline",
  gap: "0.5rem",
})

const FreePrice = styled(Typography)({
  fontSize: "4.5rem",
  fontWeight: FONT_WEIGHT.BLACK,
  color: TEXT_COLOR.PRIMARY,
  lineHeight: LINE_HEIGHT.TIGHT,
  margin: 0,
})

const FreePriceCaption = styled(Typography)({
  fontSize: FONT_SIZE.SECTION_SUBTITLE,
  color: TEXT_COLOR.SECONDARY,
  margin: 0,
})

const FreeTagline = styled(Typography)({
  fontSize: FONT_SIZE.SECTION_SUBTITLE,
  color: TEXT_COLOR.SECONDARY,
  maxWidth: 520,
  lineHeight: LINE_HEIGHT.NORMAL,
  margin: 0,
})

const FreeFeatureGrid = styled(Box)({
  display: "grid",
  gridTemplateColumns: "1fr",
  gap: "0.75rem",
  width: "100%",
  marginTop: "0.5rem",
  textAlign: "left",
  "@media (min-width: 640px)": {
    gridTemplateColumns: "repeat(2, 1fr)",
  },
})

const FreeFeature = styled(Box)({
  display: "flex",
  alignItems: "center",
  gap: "0.6rem",
  fontSize: FONT_SIZE.BODY,
  color: TEXT_COLOR.PRIMARY,
  "& .material-symbols-outlined": {
    color: ACCENT_COLOR.GREEN.color,
    fontSize: FONT_SIZE.CARD_TITLE,
  },
})

const FreeCTA = styled("button")({
  ...BUTTON_BASE,
  marginTop: "1rem",
  height: 56,
  padding: "0 3rem",
  fontSize: FONT_SIZE.SECTION_SUBTITLE,
  fontWeight: FONT_WEIGHT.BOLD,
  backgroundColor: ACCENT_COLOR.GREEN.color,
  borderRadius: 12,
  "&:hover": {
    backgroundColor: "#047857",
  },
})

const FreeFootnote = styled(Typography)({
  fontSize: FONT_SIZE.SMALL,
  color: TEXT_COLOR.SECONDARY,
  margin: 0,
})

const PremiumNote = styled(Box)({
  marginTop: "2rem",
  textAlign: "center",
  fontSize: FONT_SIZE.SMALL,
  color: TEXT_COLOR.SECONDARY,
  padding: "0.75rem 1rem",
  borderTop: `1px dashed ${BORDER_COLOR.DEFAULT}`,
  maxWidth: 720,
  marginLeft: "auto",
  marginRight: "auto",
  lineHeight: LINE_HEIGHT.NORMAL,
})

const PremiumStrong = styled("span")({
  fontWeight: FONT_WEIGHT.BOLD,
  color: TEXT_COLOR.PRIMARY,
})

const PremiumLink = styled("button")({
  background: "none",
  border: "none",
  padding: 0,
  margin: 0,
  cursor: "pointer",
  color: TEXT_COLOR.ACCENT,
  fontSize: FONT_SIZE.SMALL,
  fontWeight: FONT_WEIGHT.BOLD,
  textDecoration: "underline",
  fontFamily: "inherit",
  "&:hover": {
    textDecoration: "none",
  },
})
