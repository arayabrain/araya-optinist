import React from "react"

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
      "All published results use identical analysis workflows, eliminating methodological variability between studies.",
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

export const PublicRepository: React.FC = () => {
  return (
    <section className="landing-public-repo">
      <div className="landing-container">
        <div className="landing-public-repo-grid">
          <div className="landing-public-repo-content">
            <h2 className="landing-section-title-left">
              OptiNiSt Public Repository
            </h2>
            <p className="landing-public-repo-description">
              Public data repositories exist, but they rarely ensure consistent
              analysis methods across datasets. OptiNiSt changes that.
            </p>
            <p className="landing-public-repo-description">
              Labs can publish their analysis results alongside the exact
              workflows used—allowing other researchers to apply identical
              methods to their own data and contribute to a growing collection
              of directly comparable results.
            </p>
            <div className="landing-public-repo-benefits">
              {benefits.map((benefit, index) => (
                <div key={index} className="landing-public-repo-benefit">
                  <div className="landing-public-repo-benefit-icon">
                    <span className="material-symbols-outlined">
                      {benefit.icon}
                    </span>
                  </div>
                  <div>
                    <h4 className="landing-public-repo-benefit-title">
                      {benefit.title}
                    </h4>
                    <p className="landing-public-repo-benefit-description">
                      {benefit.description}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="landing-public-repo-visual">
            <div className="landing-public-repo-glow"></div>
            <img
              src="/images/landing-page/optinist_public_repository.png"
              alt="OptiNiSt Public Repository"
              className="landing-public-repo-image"
            />
          </div>
        </div>
      </div>
    </section>
  )
}
