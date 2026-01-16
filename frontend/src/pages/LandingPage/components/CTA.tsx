import React from "react"

export const CTA: React.FC = () => {
  return (
    <section className="landing-cta-section">
      <div className="landing-container">
        <div className="landing-cta">
          <div className="landing-cta-glow landing-cta-glow-1"></div>
          <div className="landing-cta-glow landing-cta-glow-2"></div>
          <h2 className="landing-cta-title">
            Ready to Transform Your Research?
          </h2>
          <p className="landing-cta-description">
            Join researchers worldwide using OptiNiSt to accelerate their
            scientific discoveries.
          </p>
          <div className="landing-cta-buttons">
            <button className="landing-cta-btn-primary">
              Start Free Trial
            </button>
            <button className="landing-cta-btn-secondary">
              View Documentation
            </button>
          </div>
        </div>
      </div>
    </section>
  )
}
