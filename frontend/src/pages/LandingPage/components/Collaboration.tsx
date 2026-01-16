import React from "react"

interface CollabFeature {
  icon: string
  title: string
  description: string
  color: "primary" | "cyan" | "green"
}

const collabFeatures: CollabFeature[] = [
  {
    icon: "group_add",
    title: "Team Workspaces",
    description:
      "Invite colleagues to shared workspaces. Everyone stays in sync.",
    color: "primary",
  },
  {
    icon: "history",
    title: "Version Control",
    description:
      "Track changes to workflows and experiments. Roll back anytime.",
    color: "cyan",
  },
  {
    icon: "download",
    title: "Export Anywhere",
    description:
      "Download workflows, export to NWB, or generate Snakemake pipelines.",
    color: "green",
  },
]

export const Collaboration: React.FC = () => {
  return (
    <section className="landing-collaboration">
      <div className="landing-container">
        <div className="landing-collab-grid">
          <div className="landing-collab-content">
            <span className="landing-label">Collaboration</span>
            <h2 className="landing-section-title-left">
              Work Together, Discover Faster
            </h2>
            <p className="landing-collab-description">
              Share workspaces with your team, publish results for peer review,
              and ensure every experiment is reproducible.
            </p>
            <div className="landing-collab-features">
              {collabFeatures.map((feature, index) => (
                <div key={index} className="landing-collab-feature">
                  <div
                    className={`landing-collab-icon landing-collab-icon-${feature.color}`}
                  >
                    <span className="material-symbols-outlined">
                      {feature.icon}
                    </span>
                  </div>
                  <div>
                    <h4 className="landing-collab-feature-title">
                      {feature.title}
                    </h4>
                    <p className="landing-collab-feature-description">
                      {feature.description}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="landing-collab-visual">
            <div className="landing-collab-glow"></div>
            <div className="landing-collab-card">
              <div className="landing-collab-avatars">
                <div className="landing-avatar landing-avatar-1">JD</div>
                <div className="landing-avatar landing-avatar-2">MK</div>
                <div className="landing-avatar landing-avatar-3">AS</div>
                <div className="landing-avatar landing-avatar-more">+5</div>
              </div>
              <div className="landing-collab-placeholder">
                <div className="landing-placeholder-line landing-placeholder-line-1"></div>
                <div className="landing-placeholder-line landing-placeholder-line-2"></div>
                <div className="landing-placeholder-line landing-placeholder-line-3"></div>
              </div>
              <div className="landing-collab-footer">
                <span className="landing-collab-footer-text">
                  Shared Workspace
                </span>
                <span className="landing-collab-badge">8 members</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
