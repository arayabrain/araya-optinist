import _ from "lodash"

import { createAsyncThunk } from "@reduxjs/toolkit"

import { getSubscriptionPlansApi } from "api/subscriptions/Subscriptions"
import { SUBSCRIPTION_SLICE_NAME } from "store/slice/Subscriptions/SubscriptionType"

export const getSubscriptionPlan = createAsyncThunk(
  `${SUBSCRIPTION_SLICE_NAME}/getSubscriptionPlan`,
  async (_, thunkAPI) => {
    try {
      const responseData = await getSubscriptionPlansApi()
      return responseData
    } catch (e) {
      return thunkAPI.rejectWithValue(e)
    }
  },
)
