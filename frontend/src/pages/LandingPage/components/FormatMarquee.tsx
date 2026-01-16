import React from "react"

const formats = [
  "HDF5",
  "MATLAB",
  "NWB",
  "CSV",
  "TIFF",
  "Fluorescence",
  "Behavioral",
]

export const FormatMarquee: React.FC = () => {
  return (
    <section className="landing-marquee-section" id="formats">
      <div className="landing-container">
        <p className="landing-marquee-label">Works With Your Data</p>
      </div>
      <div className="landing-marquee">
        <div className="landing-marquee-content">
          {formats.map((format, index) => (
            <span key={index} className="landing-marquee-item">
              {format}
            </span>
          ))}
        </div>
        <div className="landing-marquee-content" aria-hidden="true">
          {formats.map((format, index) => (
            <span key={`dup-${index}`} className="landing-marquee-item">
              {format}
            </span>
          ))}
        </div>
      </div>
    </section>
  )
}
