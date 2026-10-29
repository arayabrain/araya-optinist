import base64
import io

import numpy as np

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import BarData, HeatMapData, HTMLData, TimeSeriesData
from studio.app.common.schemas.outputs import PlotMetaData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.wrappers.data_utils.data_slice import _zscore
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

logger = AppLogger.get_logger()

METHODS = ("ward", "average")
METRICS = ("euclidean", "correlation")
CUT_CRITERIA = ("n_clusters", "height_fraction")
BANDS = ("std", "sem")
DENDROGRAM_MAX_ROWS = 5000
MEAN_PANELS_MAX = 30


def _choice(params, key, options):
    value = str(params[key]).strip().lower()
    assert value in options, f"Unknown {key} '{params[key]}'; expected one of {options}"
    return value


def _valid_rows(X, metric, cell_numbers):
    """Rows that can be clustered: finite, and not constant under correlation."""
    valid = np.isfinite(X).all(axis=1)
    if metric == "correlation":
        valid &= np.ptp(np.where(np.isfinite(X), X, 0), axis=1) > 0
    if not valid.all():
        logger.warning(
            "hierarchical_clustering: %d row(s) have non-finite values%s and are "
            "left out, labelled 0: %s",
            np.count_nonzero(~valid),
            " or are constant" if metric == "correlation" else "",
            np.asarray(cell_numbers)[~valid].tolist(),
        )
    return valid


def _cut(Z, criterion, n_clusters, height_fraction):
    """Flat labels and the height of the cut between merged and separate clusters."""
    from scipy.cluster.hierarchy import fcluster

    heights = Z[:, 2]
    if criterion == "n_clusters":
        labels = fcluster(Z, n_clusters, criterion="maxclust")
        # The cut lies between the last merge kept and the first merge undone
        below = heights[-n_clusters] if n_clusters <= len(heights) else 0.0
        above = heights[-n_clusters + 1] if n_clusters > 1 else heights[-1] * 1.01
        cut_height = (below + above) / 2
    else:
        cut_height = height_fraction * heights.max()
        labels = fcluster(Z, cut_height, criterion="distance")
    return labels, cut_height


def _number_from_top(labels, leaf_order):
    """Renumber clusters 1..k in the order they appear from the top of the figure."""
    mapping = {}
    for label in labels[leaf_order[::-1]]:
        mapping.setdefault(label, len(mapping) + 1)
    return np.array([mapping[label] for label in labels])


def _link_colors(Z, labels, cut_height, colors):
    """Colour each dendrogram link by its cluster, links above the cut in grey."""
    n = len(labels)
    leaf_of = np.empty(n + len(Z), dtype=int)
    leaf_of[:n] = np.arange(n)
    for i, (a, _, h, _) in enumerate(Z):
        leaf_of[n + i] = leaf_of[int(a)]

    def link_color(node_id):
        if Z[node_id - n, 2] > cut_height:
            return "#999999"
        return colors[(labels[leaf_of[node_id]] - 1) % len(colors)]

    return link_color


def _clusters_to_label(centres, sizes, min_gap):
    """Clusters whose number fits beside its block; a clash keeps the larger one."""
    kept = []
    for c in np.argsort(-sizes, kind="stable"):
        if all(abs(centres[c] - centres[d]) >= min_gap for d in kept):
            kept.append(c)
    return sorted(kept)


def _figure(
    X, index, Z, labels, leaf_order, cut_height, means, bands, response_z, color_limit
):
    """
    Static panel: sorted heatmap | cluster colour bar | dendrogram, then the cluster
    means. Rows and dendrogram leaves share one bottom-up y axis, so row i is leaf i.
    """
    from matplotlib.figure import Figure
    from matplotlib.transforms import blended_transform_factory
    from scipy.cluster.hierarchy import dendrogram

    k = len(means)
    shown = min(k, MEAN_PANELS_MAX)
    if shown < k:
        logger.warning(
            "hierarchical_clustering: figure shows the mean traces of clusters "
            "1..%d of %d; all means are in cluster_mean",
            shown,
            k,
        )
    n = len(labels)
    colors = [
        "#e7298a",
        "#e6ab02",
        "#66c2a5",
        "#a6d854",
        "#4c9be8",
        "#3a53a4",
        "#8e6bbf",
        "#e41a1c",
        "#4daf4a",
        "#a65628",
        "#f781bf",
        "#999933",
    ]
    lim = color_limit if color_limit > 0 else np.nanpercentile(np.abs(X), 99) or 1.0
    x0, x1 = float(index[0]), float(index[-1])

    fig = Figure(figsize=(11, max(6, 0.9 * shown + 1)))
    grid = fig.add_gridspec(
        max(shown, 1), 5, width_ratios=[6, 0.25, 1.6, 0.8, 4.5], wspace=0.05
    )
    ax_heat = fig.add_subplot(grid[:, 0])
    ax_bar = fig.add_subplot(grid[:, 1], sharey=ax_heat)
    ax_tree = fig.add_subplot(grid[:, 2], sharey=ax_heat)

    image = ax_heat.imshow(
        X[leaf_order],
        aspect="auto",
        origin="lower",
        cmap="RdBu_r",
        vmin=-lim,
        vmax=lim,
        interpolation="nearest",
        extent=[x0, x1, 0, 10 * n],
    )
    if x0 < 0 < x1:
        ax_heat.axvline(0, color="k", ls="--", lw=0.8)
    ax_heat.set_yticks([])
    ax_heat.set_ylabel(f"n = {n} cells, in dendrogram order")
    ax_heat.set_xlabel("index")
    fig.colorbar(image, ax=ax_heat, location="left", fraction=0.04, pad=0.12)

    sorted_labels = labels[leaf_order]
    ax_bar.imshow(
        sorted_labels[:, np.newaxis],
        aspect="auto",
        origin="lower",
        cmap=_listed(colors, k),
        vmin=0.5,
        vmax=k + 0.5,
        interpolation="nearest",
        extent=[0, 1, 0, 10 * n],
    )
    ax_bar.set_xticks([])
    ax_bar.tick_params(left=False, labelleft=False)
    # Cluster numbers right of the dendrogram, as in Patel et al. 2026 Fig 3d
    label_transform = blended_transform_factory(ax_tree.transAxes, ax_tree.transData)
    centres = np.array(
        [np.flatnonzero(sorted_labels == c).mean() for c in range(1, k + 1)]
    )
    sizes = np.bincount(labels, minlength=k + 1)[1:]
    rows_px = ax_tree.get_position().height * fig.get_figheight() * fig.dpi / n
    min_gap = 1.2 * 9 / 72 * fig.dpi / rows_px
    for c in _clusters_to_label(centres, sizes, min_gap):
        ax_tree.text(
            1.03,
            10 * centres[c] + 5,
            str(c + 1),
            ha="left",
            va="center",
            fontsize=9,
            color=colors[c % len(colors)],
            transform=label_transform,
        )

    if n <= DENDROGRAM_MAX_ROWS:
        try:
            dendrogram(
                Z,
                orientation="right",
                no_labels=True,
                ax=ax_tree,
                link_color_func=_link_colors(Z, labels, cut_height, colors),
            )
            ax_tree.axvline(cut_height, color="k", lw=0.6, ls=":")
        except RecursionError:
            logger.warning("hierarchical_clustering: dendrogram too deep to draw")
    ax_tree.set_xlabel("Linkage")
    ax_tree.tick_params(left=False, labelleft=False)
    for side in ("top", "right", "left"):
        ax_tree.spines[side].set_visible(False)
    ax_heat.set_ylim(0, 10 * n)

    for c in range(shown):
        ax = fig.add_subplot(grid[c, 4])
        color = colors[c % len(colors)]
        ax.fill_between(
            index, means[c] - bands[c], means[c] + bands[c], color=color, alpha=0.3
        )
        ax.plot(index, means[c], color=color, lw=1.2)
        ax.axhline(0, color="k", lw=0.5, ls="--")
        if x0 < 0 < x1:
            ax.axvline(0, color="k", lw=0.5, ls="--")
        title = f"Cluster {c + 1} (n = {np.count_nonzero(labels == c + 1)})"
        if response_z is not None:
            title += f", response {response_z[c]:+.2f}"
        ax.set_title(title, fontsize=7, loc="left", color=color, pad=2)
        ax.tick_params(labelsize=6)
        if c < shown - 1:
            ax.set_xticklabels([])

    buffer = io.StringIO()
    fig.savefig(buffer, format="svg", bbox_inches="tight")
    encoded = base64.b64encode(buffer.getvalue().encode()).decode()
    # An <img> keeps the SVG's own <style> and ids out of the page
    return f'<img style="width:100%" src="data:image/svg+xml;base64,{encoded}"/>'


def _listed(colors, k):
    from matplotlib.colors import ListedColormap

    return ListedColormap([colors[c % len(colors)] for c in range(max(k, 1))])


def hierarchical_clustering(
    neural_data: TimeSeriesData,
    output_dir: str,
    params: dict = None,
    **kwargs,
) -> dict(
    sorted_heatmap=HeatMapData,
    cluster_mean=TimeSeriesData,
    cluster_size=BarData,
    figure=HTMLData,
):
    """
    Agglomerative hierarchical clustering of the rows (cells) of a cells x features
    matrix, such as eta's per-cell mean or covariate_binning's tuning curves.

    Clusters are numbered 1..k from the top of the figure down. Rows that cannot be
    clustered are left out of every plot and labelled 0 in the NWB output.
    """
    from scipy.cluster.hierarchy import leaves_list, linkage

    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start hierarchical_clustering: %s", function_id)

    flattened_params = {}
    recursive_flatten_params(params, flattened_params)
    params = flattened_params

    method = _choice(params, "method", METHODS)
    metric = _choice(params, "metric", METRICS)
    criterion = _choice(params, "cut_criterion", CUT_CRITERIA)
    band_name = _choice(params, "band", BANDS)
    assert (
        method != "ward" or metric == "euclidean"
    ), "ward linkage needs metric 'euclidean'"

    X = np.asarray(neural_data.data, dtype=float)
    assert X.ndim == 2, (
        f"hierarchical_clustering needs a 2-D cells x features input, got "
        f"{type(neural_data).__name__} with shape {X.shape}"
    )
    if params["transpose"]:
        X = X.T
    index = getattr(neural_data, "index", None)
    index = np.arange(X.shape[1]) if index is None else np.asarray(index)
    cell_numbers = getattr(neural_data, "cell_numbers", None)
    cell_numbers = np.arange(X.shape[0]) if cell_numbers is None else cell_numbers
    cell_numbers = np.asarray(cell_numbers)
    if params["transpose"] or len(index) != X.shape[1]:
        index = np.arange(X.shape[1])
    if len(cell_numbers) != X.shape[0]:
        cell_numbers = np.arange(X.shape[0])

    empty = np.isnan(X).all(axis=0)
    if empty.any():
        logger.warning(
            "hierarchical_clustering: dropped %d all-NaN column(s)",
            np.count_nonzero(empty),
        )
        X, index = X[:, ~empty], index[~empty]
    assert X.shape[1] > 0, "every column of the input is NaN"

    valid = _valid_rows(X, metric, cell_numbers)
    X_valid = X[valid]
    if params["zscore_rows"]:
        X_valid = _zscore(X_valid, axis=1)
    n = len(X_valid)
    assert n >= 2, f"need at least 2 rows to cluster, {n} usable"

    n_clusters = int(params["n_clusters"])
    height_fraction = float(params["height_fraction"])
    if criterion == "n_clusters":
        assert (
            2 <= n_clusters <= n
        ), f"n_clusters must be between 2 and the {n} usable rows, got {n_clusters}"
    else:
        assert (
            0 < height_fraction <= 1
        ), f"height_fraction must be in (0, 1], got {height_fraction}"

    Z = linkage(X_valid, method=method, metric=metric)
    labels, cut_height = _cut(Z, criterion, n_clusters, height_fraction)
    leaf_order = leaves_list(Z)
    labels = _number_from_top(labels, leaf_order)
    k = labels.max()
    if criterion == "n_clusters" and k < n_clusters:
        logger.warning(
            "hierarchical_clustering: %d clusters asked, %d found (tied merges)",
            n_clusters,
            k,
        )

    sizes = np.array([np.count_nonzero(labels == c) for c in range(1, k + 1)])
    means = np.array([X_valid[labels == c].mean(axis=0) for c in range(1, k + 1)])
    std = np.full(means.shape, np.nan)
    for c in range(k):
        if sizes[c] > 1:
            std[c] = X_valid[labels == c + 1].std(axis=0, ddof=1)
    band = std / np.sqrt(sizes)[:, np.newaxis] if band_name == "sem" else std

    start, end = float(params["response_start"]), float(params["response_end"])
    response_z = None
    if end > start:
        window = (index >= start) & (index < end)
        assert window.any(), (
            f"response window [{start}, {end}) holds no index value; the index "
            f"runs from {index[0]} to {index[-1]}. Set response_end equal to "
            f"response_start to skip it"
        )
        response_z = means[:, window].mean(axis=1)

    valid_cells = cell_numbers[valid]
    all_labels = np.zeros(len(valid), dtype=float)
    all_labels[valid] = labels

    nwbfile = {}
    nwbfile[NWBDATASET.POSTPROCESS] = {
        function_id: {
            "labels": all_labels,
            "cell_numbers": cell_numbers.astype(float),
            "leaf_order": valid_cells[leaf_order].astype(float),
            "linkage": Z,
            "cut_height": np.array([cut_height], dtype=float),
            "cluster_mean": means,
            "cluster_size": sizes.astype(float),
            **(
                {"response_z": response_z.astype(float)}
                if response_z is not None
                else {}
            ),
        }
    }

    info = {}
    info["sorted_heatmap"] = HeatMapData(
        X_valid[leaf_order],
        columns=index.tolist(),
        index=valid_cells[leaf_order].tolist(),
        file_name="sorted_heatmap",
        meta=PlotMetaData(yaxis_type="category"),
    )
    info["cluster_mean"] = TimeSeriesData(
        means,
        std=band,
        index=index.tolist(),
        cell_numbers=list(range(1, k + 1)),
        file_name="cluster_mean",
    )
    info["cluster_size"] = BarData(
        sizes,
        index=["cells"],
        columns=list(range(1, k + 1)),
        file_name="cluster_size",
    )
    info["figure"] = HTMLData(
        _figure(
            X_valid,
            index,
            Z,
            labels,
            leaf_order,
            cut_height,
            means,
            band,
            response_z,
            float(params.get("color_limit", 0)),
        ),
        file_name="figure",
    )
    info["nwbfile"] = nwbfile
    return info
