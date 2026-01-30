import { Box, styled, Typography } from "@mui/material"

interface Benefit {
  icon: string
  title: string
  description: string
}

const benefits: Benefit[] = [
  {
    icon: "science",
    title: "Standardized Analysis",
    description:
      "Share workflows alongside your results so others can replicate your exact methods, eliminating methodological variability between studies.",
  },
  {
    icon: "compare",
    title: "Objective Comparisons",
    description:
      "Compare results across labs with confidence—same methods, same parameters, truly comparable outcomes.",
  },
  {
    icon: "group_work",
    title: "Invite Collaborators",
    description:
      "Invite other labs to analyze their data using your exact workflow and display results side-by-side.",
  },
]

export const PublicRepository = () => {
  return (
    <PublicRepoSection id="public-repository">
      <Container>
        <PublicRepoGrid>
          <PublicRepoContent>
            <SectionTitleLeft>
              Araya OptiNiSt Public Repository
            </SectionTitleLeft>
            <PublicRepoDescription>
              Public data repositories exist, but they rarely ensure consistent
              analysis methods across datasets. Araya OptiNiSt changes that.
            </PublicRepoDescription>
            <PublicRepoDescription>
              Labs can publish their analysis results alongside the exact
              workflows used—allowing other researchers to apply identical
              methods to their own data and contribute to a growing collection
              of directly comparable results.
            </PublicRepoDescription>
            <PublicRepoBenefits>
              {benefits.map((benefit, index) => (
                <PublicRepoBenefit key={index}>
                  <BenefitIcon>
                    <span className="material-symbols-outlined">
                      {benefit.icon}
                    </span>
                  </BenefitIcon>
                  <div>
                    <BenefitTitle>{benefit.title}</BenefitTitle>
                    <BenefitDescription>
                      {benefit.description}
                    </BenefitDescription>
                  </div>
                </PublicRepoBenefit>
              ))}
            </PublicRepoBenefits>
          </PublicRepoContent>
          <PublicRepoVisual>
            <PublicRepoGlow />
            <PublicRepoImage
              src="/images/landing-page/optinist_public_repository.png"
              alt="OptiNiSt Public Repository"
            />
          </PublicRepoVisual>
        </PublicRepoGrid>
      </Container>
    </PublicRepoSection>
  )
}

const PublicRepoSection = styled("section")({
  background: "linear-gradient(135deg, #1e3a5f 0%, #2d4a6f 50%, #1a3352 100%)",
  padding: "5rem 0",
  position: "relative",
  overflow: "hidden",
  "&::before": {
    content: "\"\"",
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundImage:
      "url(\"data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.03'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E\")",
    opacity: 0.5,
  },
})

const Container = styled(Box)({
  maxWidth: 1200,
  margin: "0 auto",
  padding: "0 1.5rem",
  position: "relative",
  zIndex: 1,
})

const PublicRepoGrid = styled(Box)({
  display: "grid",
  gridTemplateColumns: "1fr",
  gap: "3rem",
  alignItems: "center",
  "@media (min-width: 1024px)": {
    gridTemplateColumns: "1fr 1fr",
  },
})

const PublicRepoContent = styled(Box)({
  display: "flex",
  flexDirection: "column",
  gap: "1rem",
})

const SectionTitleLeft = styled(Typography)({
  fontSize: "2.5rem",
  fontWeight: 900,
  margin: 0,
  color: "white",
})

const PublicRepoDescription = styled(Typography)({
  color: "rgba(255, 255, 255, 0.85)",
  margin: 0,
  lineHeight: 1.7,
  fontSize: "1.05rem",
})

const PublicRepoBenefits = styled(Box)({
  display: "flex",
  flexDirection: "column",
  gap: "1.25rem",
  marginTop: "1rem",
})

const PublicRepoBenefit = styled(Box)({
  display: "flex",
  alignItems: "flex-start",
  gap: "1rem",
})

const BenefitIcon = styled(Box)({
  width: 44,
  height: 44,
  borderRadius: 10,
  backgroundColor: "rgba(255, 255, 255, 0.15)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  flexShrink: 0,
  color: "white",
})

const BenefitTitle = styled(Typography)({
  fontWeight: 700,
  margin: "0 0 0.25rem",
  color: "white",
})

const BenefitDescription = styled(Typography)({
  fontSize: "0.875rem",
  color: "rgba(255, 255, 255, 0.8)",
  margin: 0,
  lineHeight: 1.5,
})

const PublicRepoVisual = styled(Box)({
  position: "relative",
})

const PublicRepoGlow = styled(Box)({
  position: "absolute",
  inset: "-1rem",
  background: "rgba(255, 255, 255, 0.1)",
  filter: "blur(40px)",
  borderRadius: 20,
})

const PublicRepoImage = styled("img")({
  position: "relative",
  width: "100%",
  height: "auto",
  borderRadius: 12,
  boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.25)",
})
