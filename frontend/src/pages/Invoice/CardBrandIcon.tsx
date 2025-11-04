import React from "react"

import PropTypes from "prop-types"

interface CardBrandIconProps {
  brand?: string
  size?: number
}

const CardBrandIcon: React.FC<CardBrandIconProps> = ({ brand, size = 32 }) => {
  const getImagePath = (brandName: string): string => {
    switch (brandName.toLowerCase()) {
      case "visa":
        return "/images/card-brands/visa.png"
      case "mastercard":
        return "/images/card-brands/mastercard.png"
      case "amex":
      case "american express":
        return "/images/card-brands/amex.png"
      case "discover":
        return "/images/card-brands/discover.png"
      case "jcb":
        return "/images/card-brands/jcb.png"
      case "diners":
      case "diners club":
        return "/images/card-brands/diners.png"
      case "unionpay":
        return "/images/card-brands/unionpay.png"
      default:
        return "/images/card-brands/default.png"
    }
  }

  const getBrandName = (brandName: string): string => {
    switch (brandName.toLowerCase()) {
      case "visa":
        return "Visa"
      case "mastercard":
        return "Mastercard"
      case "amex":
      case "american express":
        return "American Express"
      case "discover":
        return "Discover"
      case "jcb":
        return "JCB"
      case "diners":
      case "diners club":
        return "Diners Club"
      case "unionpay":
        return "UnionPay"
      default:
        return "Card"
    }
  }

  if (!brand) {
    return (
      <div
        style={{
          width: size,
          height: size * 0.65, // Maintain card aspect ratio
          backgroundColor: "#6b7280",
          borderRadius: "4px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginRight: "12px",
        }}
      >
        <span style={{ color: "white", fontSize: "12px", fontWeight: "bold" }}>
          ?
        </span>
      </div>
    )
  }

  return (
    <div style={{ marginRight: "12px" }}>
      <img
        src={getImagePath(brand)}
        alt={getBrandName(brand)}
        style={{
          width: size,
          height: size * 0.65, // Credit card aspect ratio (1.586:1)
          objectFit: "contain",
          borderRadius: "4px",
        }}
        onError={(e) => {
          // Fallback to default image if specific brand image fails to load
          const target = e.target as HTMLImageElement
          if (target.src !== "/images/card-brands/default.png") {
            target.src = "/images/card-brands/default.png"
          }
        }}
      />
    </div>
  )
}

// PropTypes validation
CardBrandIcon.propTypes = {
  brand: PropTypes.string,
  size: PropTypes.number,
}

// Default props
CardBrandIcon.defaultProps = {
  brand: undefined,
  size: 32,
}

export default CardBrandIcon
