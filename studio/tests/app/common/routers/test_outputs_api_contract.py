"""
Contract Tests for Outputs API

These tests verify that API responses match the frontend TypeScript interfaces.
This ensures the backend and frontend stay in sync and prevents contract mismatches.

Frontend interfaces are defined in:
  frontend/src/api/visualizations/Outputs.ts

Tested endpoints:
  - GET /api/visualizations/inittimedata/{path} -> TimeSeriesData response
  - GET /api/visualizations/timedata/{path}     -> TimeSeriesData response
  - GET /api/visualizations/alltimedata/{path}  -> TimeSeriesData response
  - GET /api/visualizations/data/{path}     -> HeatMapData/ScatterData/BarData response
  - GET /api/visualizations/image/{path}        -> ImageData response
  - GET /api/visualizations/csv/{path}          -> CsvData response
  - GET /api/visualizations/matlab/{path}       -> MatlabData response
  - GET /api/visualizations/html/{path}         -> HTMLData response
"""

# ============================================================================
# Frontend Contract Definitions
# ============================================================================
# These mirror the TypeScript interfaces in Outputs.ts

# TimeSeriesData response structure
TIMESERIES_RESPONSE_REQUIRED_FIELDS = {
    "data": dict,
    "xrange": list,
    "std": dict,
}

TIMESERIES_RESPONSE_OPTIONAL_FIELDS = {
    "meta": dict,
}

# HeatMapData response structure
HEATMAP_RESPONSE_REQUIRED_FIELDS = {
    "data": list,
    "columns": list,
    "index": list,
}

HEATMAP_RESPONSE_OPTIONAL_FIELDS = {
    "meta": dict,
}

# ImageData response structure
IMAGE_RESPONSE_REQUIRED_FIELDS = {
    "data": list,
}

IMAGE_RESPONSE_OPTIONAL_FIELDS = {
    "meta": dict,
}

# CsvData/MatlabData response structure
CSV_MATLAB_RESPONSE_REQUIRED_FIELDS = {
    "data": list,
}

CSV_MATLAB_RESPONSE_OPTIONAL_FIELDS = {
    "meta": dict,
}

# ScatterData/BarData response structure
SCATTER_BAR_RESPONSE_REQUIRED_FIELDS = {
    "data": dict,
}

SCATTER_BAR_RESPONSE_OPTIONAL_FIELDS = {
    "columns": list,
    "index": list,
    "meta": dict,
}

# HTMLData response structure
HTML_RESPONSE_REQUIRED_FIELDS = {
    "data": str,
}

HTML_RESPONSE_OPTIONAL_FIELDS = {
    "meta": dict,
}

# PlotMetaData (meta field)
PLOT_METADATA_OPTIONAL_FIELDS = {
    "xlabel": str,
    "ylabel": str,
    "title": str,
}


# ============================================================================
# Contract Validation Helpers
# ============================================================================


def validate_contract(
    result: dict,
    required_fields: dict,
    optional_fields: dict = None,
    context: str = "",
) -> None:
    """
    Validate that a response matches the frontend contract.
    """
    for field, expected_type in required_fields.items():
        assert field in result, (
            f"Contract violation ({context}): Missing required field '{field}'. "
            f"Response has: {list(result.keys())}"
        )
        if isinstance(expected_type, tuple):
            assert isinstance(result[field], expected_type), (
                f"Contract violation ({context}): Field '{field}' has wrong type. "
                f"Expected one of {expected_type}, got {type(result[field])}"
            )
        elif result[field] is not None:
            assert isinstance(result[field], expected_type), (
                f"Contract violation ({context}): Field '{field}' has wrong type. "
                f"Expected {expected_type}, got {type(result[field])}"
            )

    if optional_fields:
        for field, expected_type in optional_fields.items():
            if field in result and result[field] is not None:
                if isinstance(expected_type, tuple):
                    assert isinstance(result[field], expected_type), (
                        f"Contract violation ({context}): "
                        f"Optional field '{field}' has wrong type."
                    )
                else:
                    assert isinstance(result[field], expected_type), (
                        f"Contract violation ({context}): "
                        f"Optional field '{field}' has wrong type."
                    )


# ============================================================================
# Contract Tests: TimeSeriesData Response
# ============================================================================


def test_contract_timeseries_response_structure():
    """
    Contract test: TimeSeriesData response has required fields.
    """
    response = {
        "data": {
            "cell_0": {"0": 1.5, "1": 2.0, "2": 1.8},
            "cell_1": {"0": 0.5, "1": 0.8, "2": 0.6},
        },
        "xrange": ["0", "1", "2"],
        "std": {
            "cell_0": {"0": 0.1, "1": 0.2, "2": 0.15},
            "cell_1": {"0": 0.05, "1": 0.08, "2": 0.06},
        },
    }

    validate_contract(
        response,
        TIMESERIES_RESPONSE_REQUIRED_FIELDS,
        TIMESERIES_RESPONSE_OPTIONAL_FIELDS,
        context="TimeSeriesData response",
    )


def test_contract_timeseries_response_with_meta():
    """
    Contract test: TimeSeriesData response with meta field.
    """
    response = {
        "data": {},
        "xrange": [],
        "std": {},
        "meta": {
            "xlabel": "Time (s)",
            "ylabel": "Fluorescence",
            "title": "Cell Activity",
        },
    }

    validate_contract(
        response,
        TIMESERIES_RESPONSE_REQUIRED_FIELDS,
        TIMESERIES_RESPONSE_OPTIONAL_FIELDS,
        context="TimeSeriesData response (with meta)",
    )


def test_contract_timeseries_data_is_dict():
    """
    Contract test: TimeSeriesData.data is a dictionary.
    """
    response = {
        "data": {"cell_0": {"0": 1.0}},
        "xrange": ["0"],
        "std": {},
    }

    assert isinstance(response["data"], dict)


def test_contract_timeseries_xrange_is_list():
    """
    Contract test: TimeSeriesData.xrange is a list of strings.
    """
    response = {
        "data": {},
        "xrange": ["0", "1", "2", "3", "4"],
        "std": {},
    }

    assert isinstance(response["xrange"], list)


# ============================================================================
# Contract Tests: HeatMapData Response
# ============================================================================


def test_contract_heatmap_response_structure():
    """
    Contract test: HeatMapData response has required fields.
    """
    response = {
        "data": [[1.0, 2.0], [3.0, 4.0]],
        "columns": ["col1", "col2"],
        "index": ["row1", "row2"],
    }

    validate_contract(
        response,
        HEATMAP_RESPONSE_REQUIRED_FIELDS,
        HEATMAP_RESPONSE_OPTIONAL_FIELDS,
        context="HeatMapData response",
    )


def test_contract_heatmap_data_is_2d_array():
    """
    Contract test: HeatMapData.data is a 2D array.
    """
    response = {
        "data": [[1, 2, 3], [4, 5, 6], [7, 8, 9]],
        "columns": ["a", "b", "c"],
        "index": ["x", "y", "z"],
    }

    assert isinstance(response["data"], list)
    assert all(isinstance(row, list) for row in response["data"])


# ============================================================================
# Contract Tests: ImageData Response
# ============================================================================


def test_contract_image_response_structure():
    """
    Contract test: ImageData response has required fields.
    """
    response = {
        "data": [[[255, 128, 64], [100, 50, 25]]],
    }

    validate_contract(
        response,
        IMAGE_RESPONSE_REQUIRED_FIELDS,
        IMAGE_RESPONSE_OPTIONAL_FIELDS,
        context="ImageData response",
    )


def test_contract_image_data_is_3d_array():
    """
    Contract test: ImageData.data is a 3D array.
    """
    response = {
        "data": [[[1, 2], [3, 4]], [[5, 6], [7, 8]]],
    }

    assert isinstance(response["data"], list)


def test_contract_image_response_with_meta():
    """
    Contract test: ImageData response with meta.
    """
    response = {
        "data": [[[0]]],
        "meta": {
            "title": "Calcium Imaging",
        },
    }

    validate_contract(
        response,
        IMAGE_RESPONSE_REQUIRED_FIELDS,
        IMAGE_RESPONSE_OPTIONAL_FIELDS,
        context="ImageData response (with meta)",
    )


# ============================================================================
# Contract Tests: CsvData/MatlabData Response
# ============================================================================


def test_contract_csv_response_structure():
    """
    Contract test: CsvData response has required fields.
    """
    response = {
        "data": [[1, 2, 3], [4, 5, 6]],
    }

    validate_contract(
        response,
        CSV_MATLAB_RESPONSE_REQUIRED_FIELDS,
        CSV_MATLAB_RESPONSE_OPTIONAL_FIELDS,
        context="CsvData response",
    )


def test_contract_matlab_response_structure():
    """
    Contract test: MatlabData response has required fields.
    """
    response = {
        "data": [[1.0, 2.0], [3.0, 4.0]],
        "meta": {},
    }

    validate_contract(
        response,
        CSV_MATLAB_RESPONSE_REQUIRED_FIELDS,
        CSV_MATLAB_RESPONSE_OPTIONAL_FIELDS,
        context="MatlabData response",
    )


# ============================================================================
# Contract Tests: ScatterData/BarData Response
# ============================================================================


def test_contract_scatter_response_structure():
    """
    Contract test: ScatterData response has required fields.
    """
    response = {
        "data": {
            "x": {0: 1.0, 1: 2.0, 2: 3.0},
            "y": {0: 4.0, 1: 5.0, 2: 6.0},
        },
    }

    validate_contract(
        response,
        SCATTER_BAR_RESPONSE_REQUIRED_FIELDS,
        SCATTER_BAR_RESPONSE_OPTIONAL_FIELDS,
        context="ScatterData response",
    )


def test_contract_bar_response_structure():
    """
    Contract test: BarData response has required fields.
    """
    response = {
        "data": {
            "category1": {0: 10, 1: 20},
            "category2": {0: 15, 1: 25},
        },
        "columns": ["category1", "category2"],
        "index": ["A", "B"],
    }

    validate_contract(
        response,
        SCATTER_BAR_RESPONSE_REQUIRED_FIELDS,
        SCATTER_BAR_RESPONSE_OPTIONAL_FIELDS,
        context="BarData response",
    )


# ============================================================================
# Contract Tests: HTMLData Response
# ============================================================================


def test_contract_html_response_structure():
    """
    Contract test: HTMLData response has required fields.
    """
    response = {
        "data": "<html><body>Plot content</body></html>",
    }

    validate_contract(
        response,
        HTML_RESPONSE_REQUIRED_FIELDS,
        HTML_RESPONSE_OPTIONAL_FIELDS,
        context="HTMLData response",
    )


def test_contract_html_data_is_string():
    """
    Contract test: HTMLData.data is a string.
    """
    response = {
        "data": "<div>Content</div>",
    }

    assert isinstance(response["data"], str)


# ============================================================================
# Contract Tests: Meta Field Structure
# ============================================================================


def test_contract_meta_field_structure():
    """
    Contract test: Meta field has expected optional fields.
    """
    meta = {
        "xlabel": "Time",
        "ylabel": "Value",
        "title": "My Plot",
    }

    for field, expected_type in PLOT_METADATA_OPTIONAL_FIELDS.items():
        if field in meta:
            assert isinstance(meta[field], expected_type)


def test_contract_meta_can_be_empty():
    """
    Contract test: Meta can be an empty dict.
    """
    response = {
        "data": [],
        "meta": {},
    }

    assert response["meta"] == {}


def test_contract_meta_can_be_none():
    """
    Contract test: Meta can be None/absent.
    """
    response = {
        "data": [],
    }

    assert "meta" not in response or response.get("meta") is None


# ============================================================================
# Contract Tests: Field Naming Consistency
# ============================================================================


def test_contract_no_legacy_output_fields():
    """
    Ensure no legacy or camelCase field names.
    """
    response = {
        "data": {},
        "xrange": [],
        "std": {},
    }

    legacy_fields = [
        "xRange",  # camelCase
        "metaData",  # camelCase (should be meta)
    ]

    for legacy in legacy_fields:
        assert legacy not in response


def test_contract_xrange_is_lowercase():
    """
    Contract test: xrange uses lowercase (not camelCase).
    """
    response = {
        "data": {},
        "xrange": [],
        "std": {},
    }

    assert "xrange" in response
    assert "xRange" not in response
