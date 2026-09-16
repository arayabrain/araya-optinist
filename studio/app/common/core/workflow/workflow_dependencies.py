import os
from collections import deque
from typing import Dict, List

from studio.app.common.core.utils.filepath_creater import get_pickle_file, join_filepath
from studio.app.common.core.workflow.workflow import Edge, Node
from studio.app.dir_path import DIRPATH


def delete_dependencies(
    workspace_id: str,
    unique_id: str,
    node_ids: List[str],
    nodeDict: Dict[str, Node],
    edgeDict: Dict[str, Edge],
):
    """Delete the pickles of node_ids and of every node downstream of them."""
    queue = deque(node_ids)

    while True:
        # terminate condition
        if len(queue) == 0:
            break

        # delete pickle
        node_id = queue.pop()
        algo_name = nodeDict[node_id].data.label

        pickle_filepath = join_filepath(
            [
                DIRPATH.OUTPUT_DIR,
                get_pickle_file(
                    workspace_id=workspace_id,
                    unique_id=unique_id,
                    node_id=node_id,
                    algo_name=algo_name,
                ),
            ]
        )

        if os.path.exists(pickle_filepath):
            os.remove(pickle_filepath)

        # 全てのedgeを見て、node_idがsourceならtargetをqueueに追加する
        for edge in edgeDict.values():
            if node_id == edge.source:
                queue.append(edge.target)
