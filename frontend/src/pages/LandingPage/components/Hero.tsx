import React from "react"

export const Hero: React.FC = () => {
  return (
    <section className="landing-hero">
      <div className="landing-hero-grid">
        <div className="landing-hero-content">
          <div className="landing-hero-text">
            <span className="landing-label">
              Visual Data Analysis for Science
            </span>
            <h1 className="landing-hero-title">
              Build. Analyze.{" "}
              <span className="landing-gradient-text">Collaborate.</span>
            </h1>
            <p className="landing-hero-description">
              The no-code platform for scientific data analysis. Create
              sophisticated pipelines visually, ensure reproducibility, and
              share discoveries with your team.
            </p>
          </div>
          <div className="landing-hero-buttons">
            <button className="landing-btn-primary landing-btn-lg">
              Start Free Trial
            </button>
          </div>
          <div className="landing-hero-badges">
            <div className="landing-badge">
              <span className="material-symbols-outlined landing-badge-icon">
                check_circle
              </span>
              <span>No coding required</span>
            </div>
            <div className="landing-badge">
              <span className="material-symbols-outlined landing-badge-icon">
                check_circle
              </span>
              <span>NWB compatible</span>
            </div>
          </div>
        </div>
        <div className="landing-hero-visual">
          <div className="landing-hero-glow"></div>
          <div className="landing-hero-card">
            <div className="landing-hero-card-header">
              <div className="landing-dot landing-dot-red"></div>
              <div className="landing-dot landing-dot-yellow"></div>
              <div className="landing-dot landing-dot-green"></div>
            </div>
            <div className="landing-workflow-preview">
              <div className="landing-workflow-node landing-workflow-node-input">
                <span>Image Input</span>
              </div>
              <div className="landing-workflow-connector"></div>
              <div className="landing-workflow-node landing-workflow-node-process">
                <span>Algorithm</span>
              </div>
              <div className="landing-workflow-connector"></div>
              <div className="landing-workflow-node landing-workflow-node-output">
                <span>Visualize</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
