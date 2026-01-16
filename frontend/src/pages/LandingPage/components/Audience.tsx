import React from "react"

interface AudienceCard {
  icon: string
  title: string
  description: string
  features: string[]
  color: "primary" | "cyan" | "green"
}

const audiences: AudienceCard[] = [
  {
    icon: "psychology",
    title: "Neuroscience Labs",
    description:
      "Analyze calcium imaging, electrophysiology, and behavioral data with specialized tools.",
    features: [
      "Calcium imaging pipelines",
      "ROI extraction & analysis",
      "NWB format export",
    ],
    color: "primary",
  },
  {
    icon: "mic",
    title: "Microscopy Researchers",
    description:
      "Build image processing pipelines for any microscopy modality without coding.",
    features: [
      "Multi-format image support",
      "Batch processing",
      "Spatial filtering tools",
    ],
    color: "cyan",
  },
  {
    icon: "school",
    title: "Educators & Students",
    description:
      "Teach data analysis concepts without the overhead of learning to code.",
    features: [
      "Visual learning interface",
      "Shareable workspaces",
      "Focus on science, not syntax",
    ],
    color: "green",
  },
]

export const Audience: React.FC = () => {
  return (
    <section className="landing-audience" id="audience">
      <div className="landing-container">
        <div className="landing-section-header-center">
          <span className="landing-label">Built For You</span>
          <h2 className="landing-section-title">Who Uses OptiNiSt?</h2>
          <p className="landing-section-subtitle">
            Empowering researchers and educators worldwide.
          </p>
        </div>
        <div className="landing-audience-grid">
          {audiences.map((audience, index) => (
            <div key={index} className="landing-audience-card">
              <div
                className={`landing-audience-icon landing-audience-icon-${audience.color}`}
              >
                <span className="material-symbols-outlined">
                  {audience.icon}
                </span>
              </div>
              <h3 className="landing-audience-title">{audience.title}</h3>
              <p className="landing-audience-description">
                {audience.description}
              </p>
              <ul className="landing-audience-features">
                {audience.features.map((feature, featureIndex) => (
                  <li key={featureIndex} className="landing-audience-feature">
                    <span
                      className={`material-symbols-outlined landing-check-${audience.color}`}
                    >
                      check_circle
                    </span>
                    <span>{feature}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
