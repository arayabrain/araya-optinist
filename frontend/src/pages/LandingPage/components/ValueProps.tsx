import React from "react"

interface ValueProp {
  icon: string
  title: string
  description: string
  color: "magenta" | "cyan" | "green" | "yellow"
}

const valueProps: ValueProp[] = [
  {
    icon: "drag_pan",
    title: "No Coding Required",
    description:
      "Build complex analysis pipelines by dragging and dropping. Focus on science, not syntax.",
    color: "magenta",
  },
  {
    icon: "history",
    title: "Reproducible Science",
    description:
      "Every analysis is saved and can be reproduced exactly. Export workflows and share with reviewers.",
    color: "cyan",
  },
  {
    icon: "share",
    title: "Collaborate Seamlessly",
    description:
      "Share workspaces with colleagues. Publish results publicly so other labs can compare using the same workflows.",
    color: "green",
  },
  {
    icon: "folder_open",
    title: "Multi-Format Support",
    description:
      "Native support for NWB, HDF5, MATLAB, CSV, and image formats. Your data, your way.",
    color: "yellow",
  },
]

export const ValueProps: React.FC = () => {
  return (
    <section className="landing-value-props">
      <div className="landing-container">
        <h2 className="landing-section-title">Why Choose OptiNiSt?</h2>
        <p className="landing-section-subtitle">
          Everything you need to go from raw data to publishable insights,
          without writing a single line of code.
        </p>
        <div className="landing-value-grid">
          {valueProps.map((prop, index) => (
            <div key={index} className="landing-value-card">
              <div
                className={`landing-value-icon landing-value-icon-${prop.color}`}
              >
                <span className="material-symbols-outlined">{prop.icon}</span>
              </div>
              <h3 className="landing-value-title">{prop.title}</h3>
              <p className="landing-value-description">{prop.description}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
