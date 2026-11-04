import numpy as np
import scipy.sparse
import skimage


def oval_filter(label_mat, sparse=False):
    """ovel_filter functionality.
    Let label_mat has shape(X, Y),
    returned array has shape (X x Y, number of cells)

    when sparse=True, this method took 137ms
    when sparse=False, this method took 325ms,
    sparse=True gave 2.5x speedup.
    spatial size was 506 x 506, number of cells after filtering was 375.
    """
    if sparse:
        pixels = []
    else:
        rois = []
    regionprops = skimage.measure.regionprops(label_mat)
    for regionprop in regionprops:
        area_ratio = (
            regionprop.axis_major_length
            * regionprop.axis_minor_length
            * np.pi
            / 4
            / regionprop.area
        )
        eccen = regionprop.eccentricity
        if area_ratio < 1.8 and eccen < 0.99:  # TODO Make configurable
            roi = (label_mat == regionprop.label).astype(np.uint8)
            if sparse:
                pixels.append(np.flatnonzero(roi))
            else:
                roi = roi.reshape(-1, 1)
                rois.append(roi)

    if sparse:
        if len(pixels) == 0:
            raise ValueError("No roi region found. Revise config and input.")
        # CSC from each region's pixels, ascending: column i is region i
        indptr = np.cumsum([0] + [len(p) for p in pixels])
        return scipy.sparse.csc_matrix(
            (np.ones(indptr[-1], np.uint8), np.concatenate(pixels), indptr),
            shape=(label_mat.size, len(pixels)),
        )
    if len(rois) == 0:
        raise ValueError("No roi region found. Revise config and input.")
    rois = np.concatenate(rois, 1)
    return rois
