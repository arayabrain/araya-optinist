import { Box, styled, Typography } from "@mui/material"

import {
  FONT_SIZE,
  FONT_WEIGHT,
  LINE_HEIGHT,
  TEXT_COLOR,
  ACCENT_COLOR,
  BG_COLOR,
  BORDER_COLOR,
  ICON_BOX,
} from "pages/LandingPage/constants"

interface Stat {
  icon: string
  value: string
  label: string
  color: "PRIMARY" | "CYAN" | "GREEN" | "YELLOW"
}

// NOTE: Replace placeholder values once publication metrics are finalised.
const stats: Stat[] = [
  {
    icon: "description",
    value: "TBD",
    label: "Peer-reviewed publications",
    color: "PRIMARY",
  },
  {
    icon: "format_quote",
    value: "TBD",
    label: "Total citations",
    color: "CYAN",
  },
  {
    icon: "account_balance",
    value: "TBD",
    label: "Research institutions",
    color: "GREEN",
  },
]

interface FeaturedPaper {
  title: string
  authors: string
  venue: string
  url?: string
}

// NOTE: Replace with real papers once available.
const featuredPapers: FeaturedPaper[] = [
  {
    title: "OptiNiSt: A No-Code Pipeline for Neural Data Analysis",
    authors: "ARAYA x OIST",
    venue: "Placeholder \u2014 to be updated",
  },
]

export const Publications = () => {
  return (
    <PublicationsSection id="publications">
      <Container>
        <SectionHeader>
          <SectionLabel>
            <span
              className="material-symbols-outlined"
              style={{ fontSize: FONT_SIZE.CARD_TITLE }}
            >
              menu_book
            </span>
            Publications &amp; Impact
          </SectionLabel>
          <SectionTitle>Trusted by Researchers Worldwide</SectionTitle>
          <SectionSubtitle>
            Araya OptiNiSt is cited in peer-reviewed publications and used by
            leading research institutions around the globe.
          </SectionSubtitle>
        </SectionHeader>

        <StatsGrid>
          {stats.map((stat, index) => (
            <StatCard key={index}>
              <StatIcon
                style={{
                  backgroundColor: ACCENT_COLOR[stat.color].bg,
                  color: ACCENT_COLOR[stat.color].color,
                }}
              >
                <span className="material-symbols-outlined">{stat.icon}</span>
              </StatIcon>
              <StatValue>{stat.value}</StatValue>
              <StatLabel>{stat.label}</StatLabel>
            </StatCard>
          ))}
        </StatsGrid>

        <FeaturedList>
          <FeaturedTitle>Featured Publications</FeaturedTitle>
          {featuredPapers.map((paper, index) => (
            <FeaturedItem key={index}>
              <FeaturedIcon>
                <span className="material-symbols-outlined">article</span>
              </FeaturedIcon>
              <FeaturedContent>
                {paper.url ? (
                  <FeaturedPaperLink
                    href={paper.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {paper.title}
                  </FeaturedPaperLink>
                ) : (
                  <FeaturedPaperTitle>{paper.title}</FeaturedPaperTitle>
                )}
                <FeaturedMeta>
                  {paper.authors} &middot; {paper.venue}
                </FeaturedMeta>
              </FeaturedContent>
            </FeaturedItem>
          ))}
        </FeaturedList>
      </Container>
    </PublicationsSection>
  )
}

const PublicationsSection = styled("section")({
  backgroundColor: BG_COLOR.WHITE,
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
  marginBottom: "3rem",
})

const SectionLabel = styled(Box)({
  display: "inline-flex",
  alignSelf: "center",
  alignItems: "center",
  gap: "0.5rem",
  backgroundColor: ACCENT_COLOR.PRIMARY.bg,
  color: TEXT_COLOR.ACCENT,
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
  maxWidth: 640,
  marginLeft: "auto",
  marginRight: "auto",
  lineHeight: LINE_HEIGHT.NORMAL,
})

const StatsGrid = styled(Box)({
  display: "grid",
  gridTemplateColumns: "1fr",
  gap: "1.5rem",
  marginBottom: "3rem",
  "@media (min-width: 768px)": {
    gridTemplateColumns: "repeat(3, 1fr)",
  },
})

const StatCard = styled(Box)({
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  textAlign: "center",
  padding: "2rem",
  borderRadius: 16,
  border: `1px solid ${BORDER_COLOR.DEFAULT}`,
  backgroundColor: BG_COLOR.CARD,
  gap: "0.75rem",
})

const StatIcon = styled(Box)({ ...ICON_BOX })

const StatValue = styled(Typography)({
  fontSize: FONT_SIZE.SECTION_TITLE,
  fontWeight: FONT_WEIGHT.BLACK,
  color: TEXT_COLOR.PRIMARY,
  margin: 0,
  lineHeight: LINE_HEIGHT.TIGHT,
})

const StatLabel = styled(Typography)({
  fontSize: FONT_SIZE.BODY,
  color: TEXT_COLOR.SECONDARY,
  margin: 0,
})

const FeaturedList = styled(Box)({
  display: "flex",
  flexDirection: "column",
  gap: "1rem",
  maxWidth: 800,
  margin: "0 auto",
})

const FeaturedTitle = styled(Typography)({
  fontSize: FONT_SIZE.CARD_TITLE,
  fontWeight: FONT_WEIGHT.BOLD,
  color: TEXT_COLOR.PRIMARY,
  marginBottom: "0.5rem",
  textAlign: "center",
})

const FeaturedItem = styled(Box)({
  display: "flex",
  alignItems: "flex-start",
  gap: "1rem",
  padding: "1.25rem 1.5rem",
  borderRadius: 12,
  border: `1px solid ${BORDER_COLOR.DEFAULT}`,
  backgroundColor: BG_COLOR.CARD,
})

const FeaturedIcon = styled(Box)({
  ...ICON_BOX,
  width: 40,
  height: 40,
  backgroundColor: ACCENT_COLOR.PRIMARY.bg,
  color: ACCENT_COLOR.PRIMARY.color,
})

const FeaturedContent = styled(Box)({
  display: "flex",
  flexDirection: "column",
  gap: "0.25rem",
  flex: 1,
})

const FeaturedPaperTitle = styled(Typography)({
  fontSize: FONT_SIZE.BODY,
  fontWeight: FONT_WEIGHT.BOLD,
  color: TEXT_COLOR.PRIMARY,
  margin: 0,
})

const FeaturedPaperLink = styled("a")({
  fontSize: FONT_SIZE.BODY,
  fontWeight: FONT_WEIGHT.BOLD,
  color: TEXT_COLOR.PRIMARY,
  margin: 0,
  textDecoration: "none",
  "&:hover": {
    color: TEXT_COLOR.ACCENT,
  },
})

const FeaturedMeta = styled(Typography)({
  fontSize: FONT_SIZE.SMALL,
  color: TEXT_COLOR.SECONDARY,
  margin: 0,
})
