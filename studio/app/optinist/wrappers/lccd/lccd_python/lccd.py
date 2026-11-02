# import argparse
# import joblib
# import json

import numpy as np
import scipy.sparse

from studio.app.optinist.wrappers.lccd.lccd_python.blob_detector import BlobDetector
from studio.app.optinist.wrappers.lccd.lccd_python.roi_integration import (
    RoiIntegration,
    filter_roi_by_area,
)


class LCCD:
    def __init__(self, config):
        self.blob_detector = BlobDetector(**config["blob_detector"])
        self.roi_integration = RoiIntegration(**config["roi_integration"])
        self.frame_divider = config["frame_divider"]

    def apply(self, v):
        # lazy loading of v will make this method memory efficient.
        # https://qiita.com/Hanjin_Liu/items/7a01c1c481161fe20a3e
        roi = None
        X, Y, T = v.shape
        for m in range(0, T // self.frame_divider):
            tm = v[:, :, m * self.frame_divider : (m + 1) * self.frame_divider]
            try:
                roi_tm = self.blob_detector.apply(tm)
            except ValueError as e:
                if "No roi region found" not in str(e):
                    raise
                continue
            if self.roi_integration.sparse:
                roi_tm = scipy.sparse.csc_matrix(roi_tm)
            elif scipy.sparse.issparse(roi_tm):
                roi_tm = roi_tm.toarray()
            roi = self.roi_integration.apply(roi_tm, roi)
        if roi is None:
            raise ValueError("No roi region found. Revise config and input.")
        # integration only filters by area when it merges, so not after one chunk
        roi = filter_roi_by_area(
            roi, self.roi_integration.min_area, self.roi_integration.max_area
        )
        if self.roi_integration.sparse:
            roi = np.array(roi.todense())
        return roi


# def main(args):
#     # args = parser.parse_args()
#     with open("config/config.json", "r") as f:
#         config = json.load(f)
#     lccd = LCCD(config)

#     v = np.load("profiles/src.npy")
#     assert len(v.shape) == 3, (
#         "input array should have"
#         "dimensions (width, height, time)"
#     )
#     roi = lccd.apply(v)
#     joblib.dump({"config": config, "roi": roi}, "out.dump")


# if __name__ == "__main__":
#     parser = argparse.ArgumentParser()
#     parser.add_argument(
#         "--conf", default="config/config.json",
#         help="lccd config file"
#     )
#     parser.add_argument("--src", help="path to input file.")
#     parser.add_argument("--dst", help="path to output file.")
#     args = parser.parse_args()
#     main(args)
