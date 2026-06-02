# Paper Filtering Criteria

## Company Context: oculo.ai
Construction tech platform that captures 360° imagery on job sites, tracks progress via AI, and forecasts project outcomes.

## Purpose
Staying abreast of the field, not actively searching for specific solutions.

---

## Quality Signals (Prioritize)

### Code Available (Massive Priority)
- GitHub link in abstract/paper
- "Code will be released" with credible track record
- Reproducible implementations

### High-Profile Groups / Authors
- Top labs: ETH Zurich, CMU, Berkeley, Stanford, Oxford, Google, Meta, NVIDIA, etc.
- Known authors in SLAM/3D vision (e.g., Davide Scaramuzza, Andrew Zisserman, etc.)
- Papers from major conferences (CVPR, ICCV, ECCV, NeurIPS, SIGGRAPH)

---

## Primary Topics (High Relevance)

### 360° / Panoramic Imaging
- 360 cameras, panoramic, omnidirectional
- Equirectangular projection, spherical images
- Panorama stitching, blending
- Wide-angle / fisheye distortion

### Construction & Buildings
- Construction site monitoring, progress tracking
- Building extraction, facade parsing
- BIM (Building Information Modeling)
- Indoor scene understanding, room layout
- Architecture, structural analysis

### SfM / SLAM / Localization
- Structure from Motion (SfM)
- SLAM (Simultaneous Localization and Mapping)
- Visual odometry, visual-inertial odometry (VIO)
- Camera pose estimation, ego-motion
- Bundle adjustment, triangulation
- Loop closure, relocalization
- Visual place recognition (VPR)

### Feature Extraction & Matching
- Keypoint detection (SIFT, ORB, SuperPoint, etc.)
- Feature descriptors, local features
- Correspondence matching, stereo matching
- Homography, fundamental matrix, epipolar geometry

### Multi-View Stereo (MVS)
- Dense reconstruction from multiple views
- Depth fusion, depth map estimation
- MVS pipelines (COLMAP, OpenMVS, etc.)
- Learning-based MVS (MVSNet variants)

---

## Secondary Topics (Moderate Relevance)

### 3D Reconstruction
- Depth estimation (monocular, stereo)
- Point cloud processing
- NeRF, Gaussian splatting (for scene representation)
- Novel view synthesis

### Change Detection & Temporal
- Change detection between images/scans
- Progress monitoring, temporal analysis
- Video understanding with spatial context

### Floorplans & Layout
- Floorplan generation/estimation
- Room layout estimation
- Indoor mapping, 2D-3D alignment

---

## Lower Priority (Tangential)

- Autonomous driving perception (if transferable to indoor/construction)
- Occupancy prediction (3D voxel representations)

### Point Clouds (Future Interest)
- Currently using sparse point clouds from SLAM
- May expand usage in future
- Efficient architectures, sparse-to-dense, point cloud completion
- Lower priority unless major advancement or directly applicable

### Segmentation (High Bar)
- Only if highly applicable or a landmark/important paper
- Indoor/architectural scene segmentation
- Instance segmentation with practical deployment angle
- Foundation models for segmentation (SAM-style) if major advancement

---

## Exclude

- Medical imaging
- Face/body/gesture recognition
- Generative art / style transfer
- NLP-only papers
- Autonomous driving without transferable 3D techniques
