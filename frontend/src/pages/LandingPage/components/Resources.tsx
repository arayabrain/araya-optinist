import React from "react"

interface ResourceLink {
  icon: string
  title: string
  description: string
  link: string
  linkText: string
  color: "primary" | "teal" | "emerald" | "amber"
}

const resources: ResourceLink[] = [
  {
    icon: "code",
    title: "GitHub Repository",
    description:
      "Explore the source code, report issues, and contribute to the project.",
    link: "https://github.com/your-org/optinist",
    linkText: "View on GitHub",
    color: "primary",
  },
  {
    icon: "menu_book",
    title: "Documentation",
    description:
      "Comprehensive guides, tutorials, and API references to get you started.",
    link: "https://docs.optinist.org",
    linkText: "Read the Docs",
    color: "teal",
  },
]

export const Resources: React.FC = () => {
  return (
    <section className="landing-resources" id="resources">
      <div className="landing-container">
        <div className="landing-section-header-center">
          <span className="landing-label">Resources</span>
          <h2 className="landing-section-title">Learn & Contribute</h2>
          <p className="landing-section-subtitle">
            Everything you need to get started and become part of the community.
          </p>
        </div>
        <div className="landing-resources-grid">
          {resources.map((resource, index) => (
            <a
              key={index}
              href={resource.link}
              target="_blank"
              rel="noopener noreferrer"
              className="landing-resource-card"
            >
              <div
                className={`landing-resource-icon landing-resource-icon-${resource.color}`}
              >
                <span className="material-symbols-outlined">
                  {resource.icon}
                </span>
              </div>
              <div className="landing-resource-content">
                <h3 className="landing-resource-title">{resource.title}</h3>
                <p className="landing-resource-description">
                  {resource.description}
                </p>
              </div>
              <div
                className={`landing-resource-link landing-resource-link-${resource.color}`}
              >
                <span>{resource.linkText}</span>
                <span className="material-symbols-outlined">arrow_forward</span>
              </div>
            </a>
          ))}
        </div>
      </div>
    </section>
  )
}
