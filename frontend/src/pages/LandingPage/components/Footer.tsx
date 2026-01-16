import React from "react"

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

export const Footer: React.FC = () => {
  return (
    <footer className="landing-footer">
      <div className="landing-container">
        <div className="landing-footer-grid">
          <div className="landing-footer-brand">
            <div className="landing-logo">
              <div className="landing-logo-icon">
                <span className="material-symbols-outlined">biotech</span>
              </div>
              <h2 className="landing-logo-text">OptiNiSt</h2>
            </div>
            <p className="landing-footer-description">
              The no-code platform for scientific data analysis. Build pipelines
              visually, ensure reproducibility, and collaborate seamlessly.
            </p>
            <div className="landing-footer-social">
              <a href="#" className="landing-social-link">
                <span className="material-symbols-outlined">public</span>
              </a>
              <a href="#" className="landing-social-link">
                <span className="material-symbols-outlined">mail</span>
              </a>
            </div>
          </div>
          {footerSections.map((section, index) => (
            <div key={index} className="landing-footer-section">
              <h4 className="landing-footer-section-title">{section.title}</h4>
              <ul className="landing-footer-links">
                {section.links.map((link, linkIndex) => (
                  <li key={linkIndex}>
                    <a href={link.href} className="landing-footer-link">
                      {link.label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="landing-footer-bottom">
          <p>&copy; 2024 OptiNiSt. Built for Science.</p>
          <span>Version 2.4.0</span>
        </div>
      </div>
    </footer>
  )
}
