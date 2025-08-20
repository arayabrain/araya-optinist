/**
 * Formats expiration date input to MM/YY format
 * @param value - Raw input value
 * @returns Formatted MM/YY string
 */
export const formatExpirationDate = (value: string): string => {
  // Remove all non-digits
  value = value.replace(/\D/g, "")

  // Limit to 4 digits max
  if (value.length > 4) {
    value = value.slice(0, 4)
  }

  // Add slash formatting and validate month
  if (value.length >= 2) {
    const month = value.slice(0, 2)
    const year = value.slice(2)
    const monthNum = parseInt(month)

    // Invalid month check
    if (monthNum > 12) {
      return month.slice(0, 1) // Keep only the first digit if month > 12
    }

    value = month + (year ? "/" + year : "")
  }

  return value
}

/**
 * Validates expiration date format and checks if it's not expired
 * @param expirationDate - Expiration date in MM/YY format
 * @returns Object with isValid boolean and error message
 */
export const validateExpirationDate = (
  expirationDate: string,
): {
  isValid: boolean
  error?: string
} => {
  if (!expirationDate.match(/^\d{2}\/\d{2}$/)) {
    return {
      isValid: false,
      error: "Please enter expiration date in MM/YY format",
    }
  }

  // Validate expiration date is not in the past
  const [month, year] = expirationDate.split("/")
  const currentDate = new Date()
  const currentYear = currentDate.getFullYear() % 100
  const currentMonth = currentDate.getMonth() + 1

  const expYear = parseInt(year)
  const expMonth = parseInt(month)

  if (
    expYear < currentYear ||
    (expYear === currentYear && expMonth < currentMonth)
  ) {
    return {
      isValid: false,
      error: "Card has expired",
    }
  }

  return { isValid: true }
}

/**
 * Formats card number with spaces for display (XXXX XXXX XXXX XXXX)
 * @param value - Raw card number input
 * @returns Formatted card number string
 */
export const formatCardNumber = (value: string): string => {
  // Remove all non-digits
  value = value.replace(/\D/g, "")

  // Limit to 16 digits
  if (value.length > 16) {
    value = value.slice(0, 16)
  }

  // Add spaces every 4 digits
  value = value.replace(/(\d{4})(?=\d)/g, "$1 ")

  return value
}

/**
 * Formats security code/CVV (removes non-digits and limits to 4 characters)
 * @param value - Raw security code input
 * @returns Formatted security code string
 */
export const formatSecurityCode = (value: string): string => {
  value = value.replace(/\D/g, "")
  if (value.length > 4) {
    value = value.slice(0, 4)
  }
  return value
}

/**
 * Validates card number length
 * @param cardNumber - Card number (can include spaces)
 * @returns Object with isValid boolean and error message
 */
export const validateCardNumber = (
  cardNumber: string,
): {
  isValid: boolean
  error?: string
} => {
  const cardNumberDigits = cardNumber.replace(/\s/g, "")

  if (cardNumberDigits.length === 0) {
    return {
      isValid: false,
      error: "Card number is required.",
    }
  }

  if (cardNumberDigits.length !== 16) {
    return {
      isValid: false,
      error: "Please enter a valid 16-digit card number",
    }
  }

  return { isValid: true }
}

/**
 * Validates card number using Luhn algorithm
 * @param cardNumber - Card number digits only
 * @returns Boolean indicating if card number is valid
 */
const _isValidLuhn = (cardNumber: string): boolean => {
  let sum = 0
  let alternate = false

  // Process digits from right to left
  for (let i = cardNumber.length - 1; i >= 0; i--) {
    let digit = parseInt(cardNumber[i], 10)

    if (alternate) {
      digit *= 2
      if (digit > 9) {
        digit = Math.floor(digit / 10) + (digit % 10)
      }
    }

    sum += digit
    alternate = !alternate
  }

  return sum % 10 === 0
}

/**
 * Formats CVV input (removes non-digits and limits to 4 characters)
 * @param value - Raw CVV input
 * @returns Formatted CVV string
 */
export const formatCVV = (value: string): string => {
  return value.replace(/\D/g, "").slice(0, 4)
}

/**
 * Validates security code/CVV length
 * @param securityCode - Security code value
 * @returns Object with isValid boolean and error message
 */
export const validateSecurityCode = (
  securityCode: string,
): {
  isValid: boolean
  error?: string
} => {
  if (securityCode.length === 0) {
    return {
      isValid: false,
      error: "Security code is required",
    }
  }

  if (securityCode.length < 3) {
    return {
      isValid: false,
      error: "Please enter a valid security code",
    }
  }

  return { isValid: true }
}

/**
 * Gets clean card number digits only (removes spaces and formatting)
 * @param cardNumber - Formatted card number
 * @returns Clean digits-only string
 */
export const getCleanCardNumber = (cardNumber: string): string => {
  return cardNumber.replace(/\s/g, "")
}
