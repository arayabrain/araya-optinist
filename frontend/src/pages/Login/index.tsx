import { ChangeEvent, FormEvent, useState } from "react"
import { useDispatch } from "react-redux"
import { Link, useNavigate } from "react-router-dom"

import { AxiosError } from "axios"

interface ErrorResponse {
  detail?: string
}

import {
  Box,
  Stack,
  styled,
  Typography,
  CircularProgress,
  Alert,
  Snackbar,
} from "@mui/material"

import Loading from "components/common/Loading"
import { resendVerificationEmail } from "store/slice/Registration/RegistrationActions"
import { getMe, login } from "store/slice/User/UserActions"
import { AppDispatch } from "store/store"

const Login = () => {
  const navigate = useNavigate()
  const dispatch: AppDispatch = useDispatch()
  const [needsVerification, setNeedsVerification] = useState(false)
  const [resendingEmail, setResendingEmail] = useState(false)
  const [showResendSnackbar, setShowResendSnackbar] = useState(false)

  const [loading, setLoading] = useState(false)
  const [errors, setErrors] = useState<{ [key: string]: string }>({
    email: "",
    password: "",
  })
  const [values, setValues] = useState<{ email: string; password: string }>({
    email: "",
    password: "",
  })

  // Handle resend verification email
  const handleResendEmail = async () => {
    if (!values.email) {
      return
    }

    setResendingEmail(true)

    try {
      const resultAction = await dispatch(resendVerificationEmail(values.email))

      if (resendVerificationEmail.fulfilled.match(resultAction)) {
        setShowResendSnackbar(true)
      }
    } catch (error) {
      console.error("Failed to resend verification email:", error)
    } finally {
      setResendingEmail(false)
    }
  }

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const errorCheck = validateSubmit()
    if (errorCheck) return

    setLoading(true)
    setNeedsVerification(false)

    dispatch(login(values))
      .unwrap()
      .then(async (_) => {
        await dispatch(getMe())
        navigate("/console")
      })
      .catch((e: AxiosError) => {
        const status = e.response?.status
        const errorDetail = (e.response?.data as ErrorResponse)?.detail

        // Check for email verification error first
        if (status === 403 && errorDetail?.includes("not verified")) {
          setNeedsVerification(true)
          setErrors({
            email:
              errorDetail ||
              "Email address not verified. Please click the verification link sent to your email.",
            password: "",
          })
        } else if (status && status >= 400 && status < 500) {
          // Handle other 4xx errors (but exclude 403 verification errors)
          setErrors({ email: "Email or password is wrong.", password: "" })
        } else {
          // Handle unexpected errors
          setErrors({
            email: "An unexpected error occurred in authentication.",
            password: "",
          })
        }
      })
      .finally(() => {
        setLoading(false)
      })
  }

  const validateSubmit = () => {
    const errors = { email: "", password: "" }
    if (!values.email) {
      errors.email = "This field is required"
    }
    if (!values.password) {
      errors.password = "This field is required"
    }
    setErrors(errors)
    return errors.password || errors.email
  }

  const onChangeValue = (event: ChangeEvent<HTMLInputElement>) => {
    const { name, value } = event.target
    setValues({ ...values, [name]: value })
    setErrors({ ...errors, [name]: !value ? "This field is required" : "" })
    // Clear verification flag when user changes input
    if (needsVerification) {
      setNeedsVerification(false)
    }
  }

  return (
    <LoginWrapper>
      <LoginContent>
        <Title data-testid="title">Sign in to your account</Title>
        <FormSignUp autoComplete="off" onSubmit={onSubmit}>
          <Box sx={{ position: "relative", mb: 2 }}>
            <LabelField>
              Email<LableRequired>*</LableRequired>
            </LabelField>
            <Input
              data-testid="email"
              autoComplete="off"
              error={!!errors.email}
              name="email"
              onChange={onChangeValue}
              value={values.email}
              placeholder="Enter your email"
            />
            <TextError data-testid="error-email">{errors.email}</TextError>
          </Box>
          <Box sx={{ position: "relative", mb: 2 }}>
            <LabelField>
              Password<LableRequired>*</LableRequired>
            </LabelField>
            <Input
              data-testid="password"
              autoComplete="off"
              error={!!errors.password}
              onChange={onChangeValue}
              name="password"
              type="password"
              value={values.password}
              placeholder="Enter your password"
            />
            <TextError data-testid="error-password">
              {errors.password}
            </TextError>
          </Box>

          {/* Verification Alert */}
          {needsVerification && (
            <Alert
              severity="warning"
              sx={{ mb: 2, fontSize: 12 }}
              action={
                <ResendButton
                  onClick={handleResendEmail}
                  disabled={resendingEmail}
                >
                  {resendingEmail ? (
                    <>
                      <CircularProgress size={12} sx={{ mr: 0.5 }} />
                      Sending...
                    </>
                  ) : (
                    "Resend Email"
                  )}
                </ResendButton>
              }
            >
              Please verify your email address to continue.
            </Alert>
          )}

          <Description>
            Forgot your password?
            <LinkWrappper to="/reset-password">Reset password</LinkWrappper>
          </Description>
          <Stack
            flexDirection="row"
            gap={2}
            mt={3}
            alignItems="center"
            justifyContent="flex-end"
          >
            <LinkWrappper to="/register">
              Don&apos;t have an account? Sign up
            </LinkWrappper>
            <ButtonLogin data-testid="button-submit" type="submit">
              SIGN IN
            </ButtonLogin>
          </Stack>
        </FormSignUp>
      </LoginContent>
      <Loading loading={loading} />

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
    </LoginWrapper>
  )
}

const LoginWrapper = styled(Box)({
  width: "100%",
  height: "100%",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
})

const LoginContent = styled(Box)({
  width: 400,
  padding: 30,
  boxShadow: "2px 1px 3px 1px rgba(0,0,0,0.1)",
  borderRadius: 4,
})

const Title = styled(Typography)({
  fontSize: 15,
  fontWeight: 600,
  marginBottom: 24,
})

const FormSignUp = styled("form")({})

const LabelField = styled(Typography)({
  fontSize: 14,
})

const LableRequired = styled("span")({
  color: "red",
  fontSize: 14,
  marginLeft: 2,
})

const Input = styled("input", {
  shouldForwardProp: (props) => props !== "error",
})<{ error: boolean }>(({ error }) => {
  return {
    width: "100%",
    height: 35,
    borderRadius: 4,
    border: "1px solid",
    borderColor: error ? "red" : "#d9d9d9",
    padding: "5px 12px",
    transition: "all 0.3s",
    outline: "none",
    boxSizing: "border-box",
    ":focus, :hover": {
      borderColor: "#1677ff",
    },
  }
})

const Description = styled(Typography)(({ theme }) => ({
  fontSize: 12,
  color: "rgba(0, 0, 0, 0.65)",
  marginTop: theme.spacing(1),
}))

const LinkWrappper = styled(Link)({
  marginLeft: 6,
  color: "#1892d1",
})

const ButtonLogin = styled("button")({
  backgroundColor: "#283237",
  color: "#ffffff",
  borderRadius: 4,
  border: "none",
  outline: "none",
  padding: "10px 20px",
  cursor: "pointer",
})

const ResendButton = styled("button")({
  backgroundColor: "transparent",
  color: "#ed6c02",
  border: "1px solid #ed6c02",
  borderRadius: 4,
  padding: "4px 12px",
  fontSize: 12,
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  fontWeight: 500,
  transition: "all 0.2s",
  "&:hover": {
    backgroundColor: "rgba(237, 108, 2, 0.08)",
  },
  "&:disabled": {
    opacity: 0.6,
    cursor: "not-allowed",
  },
})

const TextError = styled(Typography)({
  fontSize: 12,
  color: "red",
  bottom: 4,
  wordWrap: "break-word",
  wordBreak: "break-word",
  whiteSpace: "normal",
  width: "100%",
})

export default Login
