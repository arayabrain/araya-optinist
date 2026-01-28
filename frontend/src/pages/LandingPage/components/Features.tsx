import { Box, styled, Typography } from "@mui/material"

interface Feature {
  icon: string
  title: string
  description: string
  image: string
  imageAlt: string
  iconColor: "primary" | "magenta" | "green" | "yellow"
}

const features: Feature[] = [
  {
    icon: "account_tree",
    title: "Visual Workflow Builder",
    description:
      "Drag algorithm nodes onto a blank workflow, connect them visually, and configure parameters via a sidebar panel with text fields, dropdowns, and toggles. Run pipelines with one click.",
    image: "/images/landing-page/visualize_workflow_builder.png",
    imageAlt: "Visual Workflow Builder",
    iconColor: "primary",
  },
  {
    icon: "analytics",
    title: "Rich Visualization",
    description:
      "Heatmaps, time series, scatter plots, bar charts, histograms, and more. Customize colors, export publication-ready figures.",
    image: "/images/landing-page/rich_visualization.png",
    imageAlt: "Rich Visualization",
    iconColor: "magenta",
  },
  {
    icon: "frame_inspect",
    title: "Cell ROI Analysis Tools",
    description:
      "Draw, merge, and edit cell ROI directly on images. Perfect for calcium imaging and spatial analysis workflows.",
    image: "/images/landing-page/roi_analysis_tools.png",
    imageAlt: "Cell ROI Analysis Tools",
    iconColor: "green",
  },
  {
    icon: "science",
    title: "Experiment Management",
    description:
      "Track all analysis workflows and results. Compare approaches. Re-run past analyses instantly. Export data to NWB or download workflow configs for Snakemake.",
    image: "/images/landing-page/experiment_management.png",
    imageAlt: "Experiment Management",
    iconColor: "yellow",
  },
]

const iconColors = {
  primary: "#2563eb",
  magenta: "#e11d48",
  green: "#059669",
  yellow: "#d97706",
}

export const Features = () => {
  return (
    <FeaturesSection id="features">
      <Container>
        <FeaturesHeader>
          <Label>Features</Label>
          <SectionTitleLeft>Everything You Need to Analyze</SectionTitleLeft>
          <SectionDescription>
            Powerful tools that make complex data analysis accessible to
            everyone.
          </SectionDescription>
        </FeaturesHeader>
        <FeaturesGrid>
          {features.map((feature, index) => (
            <FeatureCard key={index}>
              <FeatureContent>
                <FeatureHeader>
                  <span
                    className="material-symbols-outlined"
                    style={{ color: iconColors[feature.iconColor] }}
                  >
                    {feature.icon}
                  </span>
                  <FeatureTitle>{feature.title}</FeatureTitle>
                </FeatureHeader>
                <FeatureDescription>{feature.description}</FeatureDescription>
              </FeatureContent>
              <FeatureVisual>
                <FeatureImage src={feature.image} alt={feature.imageAlt} />
              </FeatureVisual>
            </FeatureCard>
          ))}
        </FeaturesGrid>
      </Container>
    </FeaturesSection>
  )
}

const FeaturesSection = styled("section")({
  padding: "5rem 0",
})

const Container = styled(Box)({
  maxWidth: 1200,
  margin: "0 auto",
  padding: "0 1.5rem",
})

const FeaturesHeader = styled(Box)({
  display: "flex",
  flexDirection: "column",
  gap: "1rem",
  marginBottom: "4rem",
  maxWidth: 700,
})

const Label = styled(Typography)({
  color: "#2563eb",
  fontWeight: 700,
  letterSpacing: "0.1em",
  fontSize: "0.75rem",
  textTransform: "uppercase",
})

const SectionTitleLeft = styled(Typography)({
  fontSize: "2.5rem",
  fontWeight: 900,
  margin: 0,
})

const SectionDescription = styled(Typography)({
  color: "#6b7280",
  margin: 0,
})

const FeaturesGrid = styled(Box)({
  display: "grid",
  gridTemplateColumns: "1fr",
  gap: "2rem",
  "@media (min-width: 768px)": {
    gridTemplateColumns: "repeat(2, 1fr)",
  },
})

const FeatureCard = styled(Box)({
  backgroundColor: "white",
  border: "1px solid #e5e7eb",
  borderRadius: 12,
  overflow: "hidden",
  display: "flex",
  flexDirection: "column",
  transition: "box-shadow 0.3s",
  "&:hover": {
    boxShadow: "0 20px 40px -12px rgba(0, 0, 0, 0.1)",
  },
})

const FeatureContent = styled(Box)({
  padding: "1.5rem",
  display: "flex",
  flexDirection: "column",
  gap: "0.5rem",
})

const FeatureHeader = styled(Box)({
  display: "flex",
  alignItems: "center",
  gap: "0.75rem",
  marginBottom: "0.5rem",
})

const FeatureTitle = styled(Typography)({
  fontSize: "1.25rem",
  fontWeight: 700,
  margin: 0,
})

const FeatureDescription = styled(Typography)({
  fontSize: "0.875rem",
  color: "#6b7280",
  lineHeight: 1.6,
  margin: 0,
})

const FeatureVisual = styled(Box)({
  backgroundColor: "#f9fafb",
  height: 256,
  margin: "0 1.5rem 1.5rem",
  borderRadius: 8,
  border: "1px solid #e5e7eb",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  overflow: "hidden",
})

const FeatureImage = styled("img")({
  width: "100%",
  height: "100%",
  objectFit: "cover",
  borderRadius: 8,
})
