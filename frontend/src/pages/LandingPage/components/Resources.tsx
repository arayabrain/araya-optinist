import { Box, styled, Typography } from "@mui/material"

interface ResourceLink {
  icon: string
  title: string
  description: string
  link: string
  linkText: string
  color: "primary" | "teal"
}

const resources: ResourceLink[] = [
  {
    icon: "code",
    title: "GitHub Repository",
    description:
      "Explore the source code, report issues, and contribute to the project.",
    link: "https://github.com/arayabrain/optinist-for-cloud",
    linkText: "View on GitHub",
    color: "primary",
  },
  {
    icon: "menu_book",
    title: "Documentation",
    description:
      "Comprehensive guides, tutorials, and API references to get you started.",
    link: "https://optinist.readthedocs.io/en/latest/index.html",
    linkText: "Read the Docs",
    color: "teal",
  },
]

const resourceColors = {
  primary: { bg: "rgba(37, 99, 235, 0.1)", color: "#2563eb" },
  teal: { bg: "rgba(13, 148, 136, 0.1)", color: "#0d9488" },
}

export const Resources = () => {
  return (
    <ResourcesSection id="resources">
      <Container>
        <SectionHeaderCenter>
          <Label>Resources</Label>
          <SectionTitle>Learn & Contribute</SectionTitle>
          <SectionSubtitle>
            Everything you need to get started and become part of our growing
            community.
          </SectionSubtitle>
        </SectionHeaderCenter>
        <ResourcesGrid>
          {resources.map((resource, index) => (
            <ResourceCard
              key={index}
              href={resource.link}
              target="_blank"
              rel="noopener noreferrer"
            >
              <ResourceIcon
                style={{
                  backgroundColor: resourceColors[resource.color].bg,
                  color: resourceColors[resource.color].color,
                }}
              >
                <span className="material-symbols-outlined">
                  {resource.icon}
                </span>
              </ResourceIcon>
              <ResourceContent>
                <ResourceTitle>{resource.title}</ResourceTitle>
                <ResourceDescription>
                  {resource.description}
                </ResourceDescription>
              </ResourceContent>
              <ResourceLinkWrapper
                style={{ color: resourceColors[resource.color].color }}
              >
                <span>{resource.linkText}</span>
                <span className="material-symbols-outlined">arrow_forward</span>
              </ResourceLinkWrapper>
            </ResourceCard>
          ))}
        </ResourcesGrid>
      </Container>
    </ResourcesSection>
  )
}

const ResourcesSection = styled("section")({
  backgroundColor: "#f9fafb",
  padding: "5rem 0",
})

const Container = styled(Box)({
  maxWidth: 1200,
  margin: "0 auto",
  padding: "0 1.5rem",
})

const SectionHeaderCenter = styled(Box)({
  textAlign: "center",
  marginBottom: "4rem",
})

const Label = styled(Typography)({
  color: "#2563eb",
  fontWeight: 700,
  letterSpacing: "0.1em",
  fontSize: "0.75rem",
  textTransform: "uppercase",
})

const SectionTitle = styled(Typography)({
  fontSize: "1.875rem",
  fontWeight: 700,
  margin: "1rem 0 1rem",
  textAlign: "center",
})

const SectionSubtitle = styled(Typography)({
  textAlign: "center",
  color: "#6b7280",
  margin: 0,
  maxWidth: 600,
  marginLeft: "auto",
  marginRight: "auto",
})

const ResourcesGrid = styled(Box)({
  display: "grid",
  gridTemplateColumns: "1fr",
  gap: "1.5rem",
  maxWidth: 800,
  margin: "0 auto",
  "@media (min-width: 768px)": {
    gridTemplateColumns: "repeat(2, 1fr)",
  },
})

const ResourceCard = styled("a")({
  display: "flex",
  flexDirection: "column",
  gap: "1rem",
  backgroundColor: "white",
  padding: "1.5rem",
  borderRadius: 12,
  border: "1px solid #e5e7eb",
  textDecoration: "none",
  color: "inherit",
  transition: "all 0.3s",
  "&:hover": {
    boxShadow: "0 20px 40px -12px rgba(0, 0, 0, 0.1)",
    transform: "translateY(-4px)",
  },
})

const ResourceIcon = styled(Box)({
  width: 48,
  height: 48,
  borderRadius: 10,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  "& .material-symbols-outlined": {
    fontSize: "1.5rem",
  },
})

const ResourceContent = styled(Box)({
  flex: 1,
})

const ResourceTitle = styled(Typography)({
  fontSize: "1.125rem",
  fontWeight: 700,
  margin: "0 0 0.5rem",
})

const ResourceDescription = styled(Typography)({
  fontSize: "0.875rem",
  color: "#6b7280",
  margin: 0,
  lineHeight: 1.5,
})

const ResourceLinkWrapper = styled(Box)({
  display: "flex",
  alignItems: "center",
  gap: "0.5rem",
  fontSize: "0.875rem",
  fontWeight: 600,
  marginTop: "auto",
  "& .material-symbols-outlined": {
    fontSize: "1.125rem",
    transition: "transform 0.2s",
  },
  "a:hover &  .material-symbols-outlined": {
    transform: "translateX(4px)",
  },
})
