.. meta::
   :description: Araya-OptiNiSt Cloud – a no-code cloud platform for calcium imaging data analysis. Build visual pipelines, ensure reproducibility, and collaborate with NWB-compatible workflows.
   :keywords: OptiNiSt, Araya, calcium imaging, neuroscience, data analysis, no-code, NWB, Neurodata Without Borders, visual workflow, pipeline builder, ROI analysis, cloud computing, reproducible science, OIST, microscopy, open science

Araya-OptiNiSt Cloud
==========================================

**Araya-OptiNiSt Cloud** allows researchers to process and visualize their calcium imaging data entirely online. It was built by `Araya Inc. <https://www.araya.org/en/>`_ on top of `OptiNiSt <https://github.com/oist/optinist>`_, an open-source calcium imaging pipeline tool originally developed in collaboration with `OIST <https://www.oist.jp/>`_.

We believe in open, reproducible science and in making it easy to share results between labs. Araya-OptiNiSt Cloud is built around these principles:

- **Public Data Sharing**: Publish your experiments to the `public page <https://www.araya-optinist.com/public>`_, where anyone can view your results and reproduce your workflows without needing an account.
- **Cloud Computing**: Run analysis pipelines on cloud infrastructure without managing local hardware.
- **Cloud Storage**: Store your data securely in the cloud with Amazon S3-backed storage and on-demand synchronization.

**Plans** are available to suit different needs, from individual researchers to large labs.

- **Free** -- Shared compute resources with limited storage.
- **Premium** -- Dedicated compute resources with expanded storage.
- **Custom** -- Any size of compute (CPU or GPU) and storage. Additional analysis methods and plots can be added on request.
See the `Subscription page <https://www.araya-optinist.com/subscription>`_ for full details.

For inquiries, see the :doc:`other/contact` page.

About OptiNiSt
---------------
**OptiNiSt (Optical Neuroimage Studio)** helps researchers try multiple data analysis methods, visualize the results, and construct data analysis pipelines easily and quickly. OptiNiSt's data-saving format follows NWB standards.

OptiNiSt also supports reproducibility of scientific research, standardization of analysis protocols, and development of novel analysis tools as plug-ins.

Main Features
~~~~~~~~~~~~~~
- **Easy-To-Create Workflow**: create analysis pipelines easily on the GUI.
- **Visualizing analysis results**: visualize the analysis results.
- **Managing Workflows**: record and reproduce workflow pipelines easily.


.. * :ref:`modindex`
.. * :ref:`search`
.. * :ref:`genindex`

.. toctree::
  :maxdepth: 2
  :caption: User Guide:

  tutorials
  gui_guide/index
  specifications/index
  other/index
  for_developers/index

Explore Our GitHub Repository
-----------------------------
We're building in the open! You can explore the codebase, check out open issues, and contribute to the project on GitHub.

`Visit the Araya-OptiNiSt GitHub Repository <https://github.com/arayabrain/araya-optinist>`_

Citation
--------

If you use this software, please cite our paper:
`Optical Neuroimage Studio (OptiNiSt): intuitive, scalable, extendable framework for optical neuroimage data analysis <https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1013087>`_.

.. code-block:: bibtex

   @misc{OptiNiSt,
       author = {Yamane, Yukako and Li, Yuzhe and Matsumoto, Keita and Kanai, Ryota and Desforges, Miles and Gutierrez, Carlos Enrique and Doya, Kenji},
       title = {Optical Neuroimage Studio (OptiNiSt): intuitive, scalable, extendable framework for optical neuroimage data analysis},
       volume = {21},
       issue = {5},
       year = {2025},
       doi = {10.1371/journal.pcbi.1013087},
       journal = {PLOS Computational Biology}
   }

Join Our User Community on Slack
--------------------------------
We've launched a Slack workspace to provide a more casual space for discussions and interaction among users.

`Join the Optinist User Community on Slack <https://join.slack.com/t/optinist-community/shared_invite/zt-32gtn36gx-stu8ywHn6L807k95zWVUkg>`_

Feel free to use it as a space for casual conversations, product questions, requests, and feedback.

Contact Support
---------------
For questions, bug reports, or assistance, see our :doc:`other/contact` page.
