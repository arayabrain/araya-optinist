import { useState, FormEvent, ChangeEvent, useEffect } from "react"
import { useDispatch, useSelector } from "react-redux"
import { useNavigate } from "react-router-dom"

import CheckCircleIcon from "@mui/icons-material/CheckCircle"
import EmailIcon from "@mui/icons-material/Email"
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined"
import LockIcon from "@mui/icons-material/Lock"
import PersonIcon from "@mui/icons-material/Person"
import {
  Box,
  Button,
  TextField,
  Typography,
  Alert,
  styled,
  CircularProgress,
  Paper,
  Checkbox,
  FormControlLabel,
  Snackbar,
} from "@mui/material"

import {
  registerUser,
  resendVerificationEmail,
} from "store/slice/Registration/RegistrationActions"
import {
  selectRegistrationLoading,
  selectRegistrationSuccess,
  selectRegistrationError,
  selectRegistrationUser,
  selectResendEmailLoading,
  selectResendEmailSuccess,
} from "store/slice/Registration/RegistrationSelector"
import {
  clearRegistrationErrors,
  clearResendSuccess,
  clearAllRegistrationState,
} from "store/slice/Registration/RegistrationSlice"
import { AppDispatch } from "store/store"

interface FormData {
  email: string
  password: string
  confirmPassword: string
  name: string
}

const RegistrationForm = () => {
  const navigate = useNavigate()
  const dispatch = useDispatch<AppDispatch>()

  // Redux Selectors
  const loading = useSelector(selectRegistrationLoading)
  const success = useSelector(selectRegistrationSuccess)
  const error = useSelector(selectRegistrationError)
  const user = useSelector(selectRegistrationUser)
  const resendingEmail = useSelector(selectResendEmailLoading)
  const resendSuccess = useSelector(selectResendEmailSuccess)

  // Local State
  const [formData, setFormData] = useState<FormData>({
    email: "",
    password: "",
    confirmPassword: "",
    name: "",
  })

  const [validationError, setValidationError] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [showResendSnackbar, setShowResendSnackbar] = useState(false)

  // Cleanup on mount/unmount
  useEffect(() => {
    dispatch(clearRegistrationErrors())

    return () => {
      dispatch(clearAllRegistrationState())
    }
  }, [dispatch])

  // Show snackbar when resend is successful
  useEffect(() => {
    if (resendSuccess) {
      setShowResendSnackbar(true)
      dispatch(clearResendSuccess())
    }
  }, [resendSuccess, dispatch])

  // Handle input change
  const handleChange = (e: ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target
    setFormData((prev) => ({ ...prev, [name]: value }))

    // Clear validation error
    if (validationError) {
      setValidationError("")
    }

    // Clear server error
    if (error) {
      dispatch(clearRegistrationErrors())
    }
  }

  // Validate form
  const validateForm = (): boolean => {
    if (!formData.email || !formData.password || !formData.name) {
      setValidationError("Please fill in all fields")
      return false
    }

    if (formData.name.trim().length < 2) {
      setValidationError("Name must be at least 2 characters")
      return false
    }

    if (formData.password.length < 8) {
      setValidationError("Password must be at least 8 characters")
      return false
    }

    if (!/(?=.*[a-z])/.test(formData.password)) {
      setValidationError("Password must contain lowercase letters")
      return false
    }

    if (!/(?=.*[A-Z])/.test(formData.password)) {
      setValidationError("Password must contain uppercase letters")
      return false
    }

    if (!/(?=.*\d)/.test(formData.password)) {
      setValidationError("Password must contain numbers")
      return false
    }

    if (formData.password !== formData.confirmPassword) {
      setValidationError("Passwords do not match")
      return false
    }

    return true
  }

  // Handle form submission
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()

    if (!validateForm()) {
      return
    }

    const resultAction = await dispatch(
      registerUser({
        email: formData.email,
        password: formData.password,
        name: formData.name.trim(),
        organization_id: 1,
      }),
    )

    if (registerUser.fulfilled.match(resultAction)) {
      console.log("Registration successful!")
    } else {
      console.error("Registration failed:", resultAction.payload)
    }
  }

  // Handle resend email
  const handleResendEmail = async () => {
    const resultAction = await dispatch(resendVerificationEmail(formData.email))

    if (resendVerificationEmail.fulfilled.match(resultAction)) {
      console.log("Email resent successfully")
    } else {
      console.error("Email resend failed:", resultAction.payload)
    }
  }

  // Success view
  if (success) {
    return (
      <PageWrapper>
        <FormCard elevation={3}>
          <SuccessContent>
            <CheckCircleIcon sx={{ fontSize: 80, color: "#10b981", mb: 2 }} />

            <Typography variant="h4" fontWeight="bold" gutterBottom>
              Registration Almost Complete!
            </Typography>

            <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
              A verification email has been sent to{" "}
              <strong>{user?.email || formData.email}</strong>
            </Typography>

            <Alert severity="info" icon={<InfoOutlinedIcon />} sx={{ mb: 3 }}>
              <Typography variant="body2">
                To complete your registration, please check your email and click
                the verification link. You&apos;ll be able to log in once your
                email is verified.
              </Typography>
            </Alert>

            <Button
              variant="outlined"
              onClick={handleResendEmail}
              disabled={resendingEmail}
              sx={{
                mb: 2,
                borderColor: "#667eea",
                color: "#667eea",
                "&:hover": {
                  borderColor: "#5568d3",
                  backgroundColor: "rgba(102, 126, 234, 0.04)",
                },
              }}
            >
              {resendingEmail ? (
                <>
                  <CircularProgress size={16} sx={{ mr: 1 }} />
                  Sending...
                </>
              ) : (
                "Resend Verification Email"
              )}
            </Button>

            <Button
              variant="contained"
              onClick={() => navigate("/login")}
              fullWidth
              sx={{
                backgroundColor: "#667eea",
                "&:hover": {
                  backgroundColor: "#5568d3",
                },
              }}
            >
              Go to Login Page
            </Button>
          </SuccessContent>
        </FormCard>

        {/* Resend success snackbar */}
        <Snackbar
          open={showResendSnackbar}
          autoHideDuration={6000}
          onClose={() => setShowResendSnackbar(false)}
          anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        >
          <Alert
            onClose={() => setShowResendSnackbar(false)}
            severity="success"
            sx={{ width: "100%" }}
          >
            Verification email resent successfully
          </Alert>
        </Snackbar>
      </PageWrapper>
    )
  }

  // Registration form
  return (
    <PageWrapper>
      <FormCard elevation={3}>
        <Typography variant="h4" fontWeight="bold" textAlign="center" mb={1}>
          Sign Up
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          textAlign="center"
          mb={3}
        >
          Please fill in all information
        </Typography>

        <form onSubmit={handleSubmit}>
          {/* Validation or server error */}
          {(validationError || error) && (
            <Alert severity="error" sx={{ mb: 3 }}>
              {validationError || error}
            </Alert>
          )}

          {/* Name input */}
          <TextField
            fullWidth
            label="Name"
            name="name"
            value={formData.name}
            onChange={handleChange}
            disabled={loading}
            autoFocus
            sx={{ mb: 2 }}
            InputProps={{
              startAdornment: (
                <PersonIcon sx={{ mr: 1, color: "action.disabled" }} />
              ),
            }}
          />

          {/* Email input */}
          <TextField
            fullWidth
            type="email"
            label="Email Address"
            name="email"
            value={formData.email}
            onChange={handleChange}
            disabled={loading}
            sx={{ mb: 2 }}
            InputProps={{
              startAdornment: (
                <EmailIcon sx={{ mr: 1, color: "action.disabled" }} />
              ),
            }}
          />

          {/* Password input */}
          <TextField
            fullWidth
            type={showPassword ? "text" : "password"}
            label="Password"
            name="password"
            value={formData.password}
            onChange={handleChange}
            disabled={loading}
            helperText="At least 8 characters including uppercase, lowercase, and numbers"
            sx={{ mb: 2 }}
            InputProps={{
              startAdornment: (
                <LockIcon sx={{ mr: 1, color: "action.disabled" }} />
              ),
            }}
          />

          {/* Confirm password input */}
          <TextField
            fullWidth
            type={showPassword ? "text" : "password"}
            label="Confirm Password"
            name="confirmPassword"
            value={formData.confirmPassword}
            onChange={handleChange}
            disabled={loading}
            sx={{ mb: 2 }}
            InputProps={{
              startAdornment: (
                <LockIcon sx={{ mr: 1, color: "action.disabled" }} />
              ),
            }}
          />

          {/* Show password checkbox */}
          <FormControlLabel
            control={
              <Checkbox
                checked={showPassword}
                onChange={(e) => setShowPassword(e.target.checked)}
                sx={{
                  color: "#667eea",
                  "&.Mui-checked": {
                    color: "#667eea",
                  },
                }}
              />
            }
            label="Show Password"
            sx={{ mb: 2 }}
          />

          {/* Submit button */}
          <Button
            type="submit"
            variant="contained"
            fullWidth
            disabled={loading}
            startIcon={
              loading ? <CircularProgress size={20} color="inherit" /> : null
            }
            sx={{
              py: 1.5,
              fontSize: "1rem",
              fontWeight: 600,
              textTransform: "none",
              backgroundColor: "#667eea",
              "&:hover": {
                backgroundColor: "#5568d3",
                transform: "translateY(-2px)",
                boxShadow: "0 6px 20px rgba(102, 126, 234, 0.4)",
              },
              "&:disabled": {
                backgroundColor: "#9ca3af",
              },
              transition: "all 0.2s ease-in-out",
            }}
          >
            {loading ? "Registering..." : "Register"}
          </Button>
        </form>

        {/* Login link */}
        <Typography variant="body2" textAlign="center" mt={2}>
          Already have an account?{" "}
          <span
            onClick={() => navigate("/login")}
            style={{ color: "#667eea", cursor: "pointer", fontWeight: 600 }}
          >
            Login
          </span>
        </Typography>
      </FormCard>
    </PageWrapper>
  )
}

// ========================================
// Styled Components
// ========================================

const PageWrapper = styled(Box)({
  minHeight: "70vh",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: "2rem",
})

const FormCard = styled(Paper)({
  padding: "3rem",
  maxWidth: "500px",
  width: "100%",
  borderRadius: "1rem",
})

const SuccessContent = styled(Box)({
  textAlign: "center",
})

export default RegistrationForm
