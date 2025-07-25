import _ from "lodash"

import { createAsyncThunk } from "@reduxjs/toolkit"

import {
  getSubscriptionPlansApi,
  getUserSubscriptionApi,
} from "api/subscriptions/Subscriptions"
import { SUBSCRIPTION_SLICE_NAME } from "store/slice/Subscriptions/SubscriptionType"

export const getSubscriptionPlan = createAsyncThunk(
  `${SUBSCRIPTION_SLICE_NAME}/getSubscriptionPlan`,
  async (_, thunkAPI) => {
    try {
      const response = await getSubscriptionPlansApi()
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)

export const getUserSubscription = createAsyncThunk(
  `${SUBSCRIPTION_SLICE_NAME}/getUserSubscription`,
  async (userId: number, thunkAPI) => {
    try {
      const response = await getUserSubscriptionApi(userId)
      return response
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)
