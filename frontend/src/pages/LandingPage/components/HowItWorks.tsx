import React from "react"

interface Step {
  number: string
  title: string
  description: string
  color: "primary" | "cyan" | "green"
}

const steps: Step[] = [
  {
    number: "1",
    title: "Upload Your Data",
    description:
      "Import images, HDF5, MATLAB, CSV, or NWB files. Organize in workspaces.",
    color: "primary",
  },
  {
    number: "2",
    title: "Build Your Pipeline",
    description:
      "Drag algorithms onto the canvas, connect nodes, configure parameters visually.",
    color: "cyan",
  },
  {
    number: "3",
    title: "Analyze & Share",
    description:
      "Run pipelines, visualize results, export figures, and share with your team.",
    color: "green",
  },
]

export const HowItWorks: React.FC = () => {
  return (
    <section className="landing-how-it-works">
      <div className="landing-container">
        <div className="landing-section-header-center">
          <span className="landing-label">Simple Workflow</span>
          <h2 className="landing-section-title">
            From Data to Insights in 3 Steps
          </h2>
          <p className="landing-section-subtitle">
            No complex setup. No coding. Just results.
          </p>
        </div>
        <div className="landing-steps-grid">
          {steps.map((step, index) => (
            <div key={index} className="landing-step">
              <div
                className={`landing-step-number landing-step-number-${step.color}`}
              >
                <span>{step.number}</span>
              </div>
              <h3 className="landing-step-title">{step.title}</h3>
              <p className="landing-step-description">{step.description}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
