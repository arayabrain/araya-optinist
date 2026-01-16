import React from "react"

export const Features: React.FC = () => {
  return (
    <section className="landing-features" id="features">
      <div className="landing-container">
        <div className="landing-features-header">
          <span className="landing-label">Features</span>
          <h2 className="landing-section-title-left">
            Everything You Need to Analyze
          </h2>
          <p className="landing-section-description">
            Powerful tools that make complex data analysis accessible to
            everyone.
          </p>
        </div>
        <div className="landing-features-grid">
          {/* Feature 1: Visual Workflow Builder */}
          <div className="landing-feature-card">
            <div className="landing-feature-content">
              <div className="landing-feature-header">
                <span className="material-symbols-outlined landing-feature-icon-primary">
                  account_tree
                </span>
                <h3 className="landing-feature-title">
                  Visual Workflow Builder
                </h3>
              </div>
              <p className="landing-feature-description">
                Drag algorithm nodes onto a canvas, connect them visually, and
                configure parameters through intuitive forms. Run pipelines with
                one click.
              </p>
            </div>
            <div className="landing-feature-visual">
              <img
                src="/images/landing-page/visualize_workflow_builder.png"
                alt="Visual Workflow Builder"
                className="landing-feature-image"
              />
            </div>
          </div>

          {/* Feature 2: Rich Visualization */}
          <div className="landing-feature-card">
            <div className="landing-feature-content">
              <div className="landing-feature-header">
                <span className="material-symbols-outlined landing-feature-icon-magenta">
                  analytics
                </span>
                <h3 className="landing-feature-title">Rich Visualization</h3>
              </div>
              <p className="landing-feature-description">
                Heatmaps, time series, scatter plots, bar charts, histograms,
                and more. Customize colors, export publication-ready figures.
              </p>
            </div>
            <div className="landing-feature-visual">
              <img
                src="/images/landing-page/rich_visualization.png"
                alt="Rich Visualization"
                className="landing-feature-image"
              />
            </div>
          </div>

          {/* Feature 3: ROI Analysis */}
          <div className="landing-feature-card">
            <div className="landing-feature-content">
              <div className="landing-feature-header">
                <span className="material-symbols-outlined landing-feature-icon-green">
                  frame_inspect
                </span>
                <h3 className="landing-feature-title">ROI Analysis Tools</h3>
              </div>
              <p className="landing-feature-description">
                Draw, merge, and edit regions of interest directly on images.
                Perfect for calcium imaging and spatial analysis workflows.
              </p>
            </div>
            <div className="landing-feature-visual">
              <img
                src="/images/landing-page/roi_analysis_tools.png"
                alt="ROI Analysis Tools"
                className="landing-feature-image"
              />
            </div>
          </div>

          {/* Feature 4: Experiment Management */}
          <div className="landing-feature-card">
            <div className="landing-feature-content">
              <div className="landing-feature-header">
                <span className="material-symbols-outlined landing-feature-icon-yellow">
                  science
                </span>
                <h3 className="landing-feature-title">Experiment Management</h3>
              </div>
              <p className="landing-feature-description">
                Track all runs and results. Compare approaches. Reproduce past
                experiments instantly. Export to NWB and Snakemake formats.
              </p>
            </div>
            <div className="landing-feature-visual">
              <img
                src="/images/landing-page/experiment_management.png"
                alt="Experiment Management"
                className="landing-feature-image"
              />
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
