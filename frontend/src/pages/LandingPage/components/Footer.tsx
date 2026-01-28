import { Box, styled, Typography } from "@mui/material"

interface FooterLink {
  label: string
  href: string
}

interface FooterSection {
  title: string
  links: FooterLink[]
}

const footerSections: FooterSection[] = [
  {
    title: "Product",
    links: [
      { label: "Features", href: "#" },
      { label: "Workflow Builder", href: "#" },
      { label: "Visualization", href: "#" },
      { label: "Pricing", href: "#" },
    ],
  },
  {
    title: "Resources",
    links: [
      { label: "Documentation", href: "#" },
      { label: "Tutorials", href: "#" },
      { label: "API Reference", href: "#" },
      { label: "Community", href: "#" },
    ],
  },
  {
    title: "Company",
    links: [
      { label: "About Us", href: "#" },
      { label: "Contact", href: "#" },
      { label: "Privacy Policy", href: "#" },
      { label: "Terms of Service", href: "#" },
    ],
  },
]

export const Footer = () => {
  return (
    <FooterWrapper>
      <Container>
        <FooterGrid>
          <FooterBrand>
            <Logo>
              <LogoIcon>
                <span className="material-symbols-outlined">biotech</span>
              </LogoIcon>
              <LogoText>OptiNiSt</LogoText>
            </Logo>
            <FooterDescription>
              The no-code platform for scientific data analysis. Build pipelines
              visually, ensure reproducibility, and collaborate seamlessly.
            </FooterDescription>
            <FooterSocial>
              <SocialLink href="#">
                <span className="material-symbols-outlined">public</span>
              </SocialLink>
              <SocialLink href="#">
                <span className="material-symbols-outlined">mail</span>
              </SocialLink>
            </FooterSocial>
          </FooterBrand>
          {footerSections.map((section, index) => (
            <FooterSectionWrapper key={index}>
              <FooterSectionTitle>{section.title}</FooterSectionTitle>
              <FooterLinks>
                {section.links.map((link, linkIndex) => (
                  <li key={linkIndex}>
                    <FooterLink href={link.href}>{link.label}</FooterLink>
                  </li>
                ))}
              </FooterLinks>
            </FooterSectionWrapper>
          ))}
        </FooterGrid>
        <FooterBottom>
          <p>&copy; 2024 OptiNiSt. Built for Science.</p>
          <span>Version 2.4.0</span>
        </FooterBottom>
      </Container>
    </FooterWrapper>
  )
}

const FooterWrapper = styled("footer")({
  backgroundColor: "white",
  borderTop: "1px solid #e5e7eb",
  padding: "4rem 0",
})

const Container = styled(Box)({
  maxWidth: 1200,
  margin: "0 auto",
  padding: "0 1.5rem",
})

const FooterGrid = styled(Box)({
  display: "grid",
  gridTemplateColumns: "repeat(2, 1fr)",
  gap: "3rem",
  "@media (min-width: 768px)": {
    gridTemplateColumns: "repeat(4, 1fr)",
  },
  "@media (min-width: 1024px)": {
    gridTemplateColumns: "2fr 1fr 1fr 1fr",
  },
})

const FooterBrand = styled(Box)({
  gridColumn: "span 2",
  "@media (min-width: 1024px)": {
    gridColumn: "span 1",
  },
})

const Logo = styled(Box)({
  display: "flex",
  alignItems: "center",
  gap: "0.5rem",
})

const LogoIcon = styled(Box)({
  width: 32,
  height: 32,
  borderRadius: 6,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  color: "#2563eb",
})

const LogoText = styled(Typography)({
  fontSize: "1.25rem",
  fontWeight: 700,
  letterSpacing: "-0.025em",
})

const FooterDescription = styled(Typography)({
  fontSize: "0.875rem",
  color: "#6b7280",
  margin: "1.5rem 0",
  maxWidth: 300,
  lineHeight: 1.6,
})

const FooterSocial = styled(Box)({
  display: "flex",
  gap: "1rem",
})

const SocialLink = styled("a")({
  color: "#6b7280",
  transition: "color 0.2s",
  "&:hover": {
    color: "#2563eb",
  },
})

const FooterSectionWrapper = styled(Box)({})

const FooterSectionTitle = styled(Typography)({
  fontWeight: 700,
  margin: "0 0 1.5rem",
})

const FooterLinks = styled("ul")({
  listStyle: "none",
  padding: 0,
  margin: 0,
  display: "flex",
  flexDirection: "column",
  gap: "1rem",
})

const FooterLink = styled("a")({
  fontSize: "0.875rem",
  color: "#6b7280",
  textDecoration: "none",
  transition: "color 0.2s",
  "&:hover": {
    color: "#2563eb",
  },
})

const FooterBottom = styled(Box)({
  maxWidth: 1200,
  margin: "4rem auto 0",
  paddingTop: "2rem",
  borderTop: "1px solid #e5e7eb",
  display: "flex",
  flexDirection: "column",
  gap: "1rem",
  alignItems: "center",
  justifyContent: "space-between",
  fontSize: "0.75rem",
  color: "#6b7280",
  "@media (min-width: 640px)": {
    flexDirection: "row",
  },
  "& p": {
    margin: 0,
  },
})
