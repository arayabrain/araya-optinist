(debugging)=
Debugging
=================
This section describes the various debugging methods included in OptiNiSt.

### IPython notebooks

OptiNiSt provides several IPynb notebooks in the **notebooks** folder: [caiman.ipynb](https://github.com/arayabrain/araya-optinist/blob/main/notebooks/caiman.ipynb), [suite2p.ipynb](https://github.com/arayabrain/araya-optinist/blob/main/notebooks/suite2p.ipynb), [lccd.ipynb](https://github.com/arayabrain/araya-optinist/blob/main/notebooks/lccd.ipynb). These may be used for assessing where in the code an error is occurring. This can be useful when the Conda environment loads, however, somewhere in the ROI detection or plotting an error occurs.

The **notebooks** folder also contains an IPynb notebook for adding your own algorithm, see [here](Add-your-algorithm) for details.

### Back-compatibility (previous OptiNiSt versions)

**Python version**
Note that version 1 of OptiNiSt used python 3.8, while version 2 uses python 3.11. This difference may cause some errors to occur when upgrading version. Please refer to the [OptiNiSt Wiki FAQ](https://github.com/arayabrain/araya-optinist/wiki/FAQ) for up-to-date solutions.

**IPython parameter conversion notebook**
In OptiNiSt version 2, the parameter input structure has been reorganised. Workflows created in OptiNiSt version 1 and reproduced in version 2, as well as [workflow.yaml](ImportWorkflowYaml) produced and saved in version 1 and imported in version 2, will not work.

To reproduce a version 1 Workflow, a conversion script is provided, in the form of a IPython notebook. Follow this procedure:

1. Download workflow from Record tab
2. Open notebooks/yaml-converter.ipynb
3. Setup environment following instructions at the top of yaml-converter.ipynb
4. Convert files using the section at the bottom of yaml-converter.ipynb
5. Upload converted .yaml files on OptiNiSt Workflow page

```python
input_file = ".yaml"
output_file = ".yaml" # any name you want
convert_workflow_file(input_file, output_file)
```

### OptiNiSt wiki FAQ
For responses to common error messages, check the [OptiNiSt Wiki FAQ](https://github.com/arayabrain/araya-optinist/wiki/FAQ) page, and existing issues on [OptiNiSt git](https://github.com/arayabrain/araya-optinist/issues). Please create an new issue describing anything not covered on these pages.

### Contact Support
If you are unable to resolve your issue using the resources above, contact us at optinist-support@araya.org.
