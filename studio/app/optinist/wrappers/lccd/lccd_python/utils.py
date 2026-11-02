import numpy as np
import scipy.signal
import scipy.sparse


def matlab_conv2(x, y, mode="same"):
    if mode == "same":
        # https://stackoverflow.com/a/38355889
        return np.rot90(
            scipy.signal.convolve2d(np.rot90(x, 2), np.rot90(y, 2), mode="same"), 2
        )
    if mode == "valid":
        # https://stackoverflow.com/questions/43270274/equivalent-of-matlab-filter2filter-image-valid-in-python
        # Still there is some difference
        return scipy.signal.convolve2d(x, np.rot90(y, 2), mode="valid")


def insert_binary_col_binary_csc(mat, indices, i=None):
    """
    Insert a column into a binary csc sparse matrix as ith column.
    The column to be inserted is a binary column vector.
    The indices of nonzero elements is given as argument indices.
    if i is None, insert the column as the first column of mat.
    Mayber faster than specified i.

    This method is in-place operation.

    Parameters
    ----------
    mat: scipy.sparse.csc_matrix
    i: int
    indices: numpy.array
        The indices of nonzero elements in the column to be inserted.


    Let mat = scipy.sparse.csc_matrix(np.array([
        [1, 0, 1],
        [1, 1, 1],
        [0, 0, 0],
    ]))
    i = 1
    indices = np.array([2]).
    The column to be inserted is
    [
        [0],
        [0],
        [1],
    ]
    insert_binary_col_binary_csc(mat, indices, i) changes mat to be
    [
        [1, 0, 0, 1],
        [1, 0, 1, 1],
        [0, 1, 0, 0],
    ]       ^ Inserted column

    When indices = np.array([0, 2]),
    The column to be inserted is
    [
        [1],
        [0],
        [1],
    ]
    and insert_binary_col_binary_csc(mat, indices, i) changes mat to be
    [
        [1, 1, 0, 1],
        [1, 0, 1, 1],
        [0, 1, 0, 0],
    ]       ^ Inserted column
    """

    if not isinstance(mat, scipy.sparse.csc_matrix):
        raise ValueError("works only for CSC format -- use .tocsc() first")
    assert i <= mat.shape[1]
    mat.data = np.ones(len(mat.data) + len(indices), mat.dtype)
    if i is None:
        i = 0
    if i == 0:
        mat.indptr = np.concatenate(
            [np.array([0, len(indices)]), mat.indptr[1:] + len(indices)]
        )
        mat.indices = np.concatenate([indices, mat.indices])
    else:
        mat.indptr = np.concatenate(
            [
                mat.indptr[: i + 1],
                np.array([len(indices) + mat.indptr[i]]),
                len(indices) + mat.indptr[i + 1 :],
            ]
        )
        mat.indices = np.concatenate(
            [mat.indices[: mat.indptr[i]], indices, mat.indices[mat.indptr[i] :]]
        )
    mat._shape = (mat._shape[0], mat._shape[1] + 1)


def delete_row_csr(mat, i):
    """https://stackoverflow.com/a/13078768"""
    mat = mat.copy()
    if not isinstance(mat, scipy.sparse.csr_matrix):
        raise ValueError("works only for CSR format -- use .tocsr() first")
    n = mat.indptr[i + 1] - mat.indptr[i]
    if n > 0:
        mat.data[mat.indptr[i] : -n] = mat.data[mat.indptr[i + 1] :]
        mat.data = mat.data[:-n]
        mat.indices[mat.indptr[i] : -n] = mat.indices[mat.indptr[i + 1] :]
        mat.indices = mat.indices[:-n]
    mat.indptr[i:-1] = mat.indptr[i + 1 :]
    mat.indptr[i:] -= n
    mat.indptr = mat.indptr[:-1]
    mat._shape = (mat._shape[0] - 1, mat._shape[1])
    return mat


def delete_col_csc(mat, i):
    """https://stackoverflow.com/a/13078768"""
    mat = mat.copy()
    if not isinstance(mat, scipy.sparse.csc_matrix):
        raise ValueError("works only for CSC format -- use .tocsc() first")
    n = mat.indptr[i + 1] - mat.indptr[i]
    if n > 0:
        mat.data[mat.indptr[i] : -n] = mat.data[mat.indptr[i + 1] :]
        mat.data = mat.data[:-n]
        mat.indices[mat.indptr[i] : -n] = mat.indices[mat.indptr[i + 1] :]
        mat.indices = mat.indices[:-n]
    mat.indptr[i:-1] = mat.indptr[i + 1 :]
    mat.indptr[i:] -= n
    mat.indptr = mat.indptr[:-1]
    mat._shape = (mat._shape[0], mat._shape[1] - 1)
    return mat


def delete_col_csc_inplace(mat, i):
    """https://stackoverflow.com/a/13078768"""
    if not isinstance(mat, scipy.sparse.csc_matrix):
        raise ValueError("works only for CSC format -- use .tocsc() first")
    n = mat.indptr[i + 1] - mat.indptr[i]
    if n > 0:
        mat.data[mat.indptr[i] : -n] = mat.data[mat.indptr[i + 1] :]
        mat.data = mat.data[:-n]
        mat.indices[mat.indptr[i] : -n] = mat.indices[mat.indptr[i + 1] :]
        mat.indices = mat.indices[:-n]
    mat.indptr[i:-1] = mat.indptr[i + 1 :]
    mat.indptr[i:] -= n
    mat.indptr = mat.indptr[:-1]
    mat._shape = (mat._shape[0], mat._shape[1] - 1)
    return mat


def count_nonzero_values_in_col_csc(mat, i):
    """
    count nonzero values of mat's ith column
    equivalent to np.sum(mat[:, i]>0),
    mat[:, i].count_nonzero()
    and np.count_nonzero(mat[:, i].toarray())
    """
    if not isinstance(mat, scipy.sparse.csc_matrix):
        raise ValueError("works only for CSC format -- use .tocsc() first")
    return mat.indptr[i + 1] - mat.indptr[i]


def intersect_unique_sorted_1d(arr1, arr2):
    """
    Assuming each array is sorted and unique,
    this method is faster than numpy.intersect1d by 1.5 times
    """

    def new_merge(a, b):
        """https://stackoverflow.com/a/54131815"""
        if len(a) < len(b):
            b, a = a, b
        c = np.empty(len(a) + len(b), dtype=a.dtype)
        b_indices = np.arange(len(b)) + np.searchsorted(a, b)
        a_indices = np.ones(len(c), dtype=bool)
        a_indices[b_indices] = False
        c[b_indices] = b
        c[a_indices] = a
        return c

    aux = new_merge(arr1, arr2)
    mask = aux[1:] == aux[:-1]
    int1d = aux[:-1][mask]
    return int1d
