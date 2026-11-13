# Mimic test - does the house playbook make an analyst think like the team?

Playbook distilled from 43 TRAIN markets only; tested on 15 held-out markets + the gearbox. Lower is better except rank correlation (higher = segment growth ordering closer to the human's).

| Group | Arm | global_cagr_err | geo_share_mae | geo_gap_mae | seg_share_mae | seg_premium_mae | seg_rank_corr |
|---|---|---|---|---|---|---|---|
| held-out corpus | A_no_playbook | 1.21 pp | 2.86 pp | 1.08 pp | 6.66 pp | 1.16 pp | +0.38 |
| held-out corpus | B_playbook | 1.33 pp | 3.94 pp | 0.81 pp | 5.88 pp | 1.01 pp | +0.49 |
| held-out corpus | C_playbook_calibrated | 1.28 pp | 3.66 pp | 0.98 pp | 6.07 pp | 1.03 pp | +0.49 |
| gearbox | A_no_playbook | 0.92 pp | 3.19 pp | 1.07 pp | 7.12 pp | 1.13 pp | -0.23 |
| gearbox | B_playbook | 1.22 pp | 7.00 pp | 1.00 pp | 6.88 pp | 0.94 pp | +0.06 |
| gearbox | C_playbook_calibrated | 1.42 pp | 5.00 pp | 0.94 pp | 6.33 pp | 1.04 pp | +0.06 |

## Per market

| Market | Arm | global_cagr_err | geo_share_mae | geo_gap_mae | seg_share_mae | seg_premium_mae | seg_rank_corr |
|---|---|---|---|---|---|---|---|
| Global Adhesives & Sealants Market | A_no_playbook | 0.58 pp | 2.33 pp | 0.67 pp | 3.59 pp | 0.72 pp | +0.81 |
| Global Adhesives & Sealants Market | B_playbook | 0.58 pp | 3.00 pp | 0.45 pp | 4.16 pp | 0.52 pp | +0.82 |
| Global Adhesives & Sealants Market | C_playbook_calibrated | 0.58 pp | 2.00 pp | 0.52 pp | 3.85 pp | 0.76 pp | +0.71 |
| Global Process Analyzer Market | A_no_playbook | 0.20 pp | 2.36 pp | 0.66 pp | 3.04 pp | 0.55 pp | +0.85 |
| Global Process Analyzer Market | B_playbook | 0.50 pp | 3.68 pp | 0.35 pp | 3.00 pp | 0.54 pp | +0.71 |
| Global Process Analyzer Market | C_playbook_calibrated | 0.40 pp | 3.56 pp | 0.53 pp | 3.09 pp | 0.57 pp | +0.76 |
| Latin America Bone Cement Market | A_no_playbook | 1.00 pp | 1.80 pp | 1.27 pp | 8.22 pp | 0.98 pp | -0.23 |
| Latin America Bone Cement Market | B_playbook | 1.40 pp | 1.05 pp | 1.27 pp | 4.95 pp | 1.46 pp | -0.23 |
| Latin America Bone Cement Market | C_playbook_calibrated | 0.80 pp | 0.59 pp | 1.48 pp | 3.06 pp | 1.09 pp | -0.23 |
| Global Spectral Sensor Market | A_no_playbook | 2.02 pp | 1.28 pp | 1.37 pp | 10.28 pp | 0.95 pp | +0.97 |
| Global Spectral Sensor Market | B_playbook | 0.28 pp | 1.80 pp | 0.59 pp | 8.77 pp | 0.71 pp | +0.94 |
| Global Spectral Sensor Market | C_playbook_calibrated | 0.72 pp | 1.48 pp | 0.74 pp | 9.82 pp | 0.69 pp | +0.81 |
| U.S., China & Europe Glass Packaging Market | A_no_playbook | 1.76 pp | 1.80 pp | 0.31 pp | 5.26 pp | 0.86 pp | +0.50 |
| U.S., China & Europe Glass Packaging Market | B_playbook | 1.44 pp | 13.20 pp | 0.54 pp | 6.28 pp | 0.74 pp | +0.92 |
| U.S., China & Europe Glass Packaging Market | C_playbook_calibrated | 0.16 pp | 13.20 pp | 0.53 pp | 4.21 pp | 0.84 pp | +0.76 |
| Global Head End System Market | A_no_playbook | 0.26 pp | 2.48 pp | 1.64 pp | 4.39 pp | 1.47 pp | +0.59 |
| Global Head End System Market | B_playbook | 1.44 pp | 1.56 pp | 1.05 pp | 5.22 pp | 1.06 pp | +0.42 |
| Global Head End System Market | C_playbook_calibrated | 0.76 pp | 2.92 pp | 0.84 pp | 5.02 pp | 1.31 pp | +0.60 |
| Global High Intensity Focused Ultrasound (HIFU) Ma | A_no_playbook | 0.50 pp | 1.63 pp | 1.41 pp | 10.79 pp | 1.61 pp | +0.25 |
| Global High Intensity Focused Ultrasound (HIFU) Ma | B_playbook | 1.20 pp | 1.33 pp | 1.89 pp | 10.25 pp | 1.58 pp | +0.25 |
| Global High Intensity Focused Ultrasound (HIFU) Ma | C_playbook_calibrated | 1.50 pp | 0.97 pp | 0.79 pp | 11.89 pp | 1.34 pp | +0.10 |
| Saudi Arabia Aluminium & Wooden Kitchens Market | A_no_playbook | 4.21 pp | 1.46 pp | 1.48 pp | 2.44 pp | 1.16 pp | +0.54 |
| Saudi Arabia Aluminium & Wooden Kitchens Market | B_playbook | 2.41 pp | 2.41 pp | 1.09 pp | 2.62 pp | 1.09 pp | +0.74 |
| Saudi Arabia Aluminium & Wooden Kitchens Market | C_playbook_calibrated | 3.31 pp | 1.32 pp | 1.73 pp | 3.54 pp | 1.00 pp | +0.82 |
| India Smart Hospitals & Digital Transformation Mar | A_no_playbook | 0.48 pp | 1.33 pp | 1.37 pp | 6.53 pp | 1.21 pp | +0.50 |
| India Smart Hospitals & Digital Transformation Mar | B_playbook | 0.78 pp | 1.33 pp | 1.04 pp | 7.22 pp | 1.15 pp | +0.50 |
| India Smart Hospitals & Digital Transformation Mar | C_playbook_calibrated | 1.78 pp | 2.00 pp | 1.60 pp | 7.96 pp | 1.26 pp | +0.50 |
| Global Adhesives & Bonding Market | A_no_playbook | 0.82 pp | 4.30 pp | 0.60 pp | 3.63 pp | 0.80 pp | +0.57 |
| Global Adhesives & Bonding Market | B_playbook | 1.42 pp | 3.63 pp | 0.52 pp | 3.73 pp | 0.81 pp | +0.50 |
| Global Adhesives & Bonding Market | C_playbook_calibrated | 1.22 pp | 2.80 pp | 0.60 pp | 4.01 pp | 0.82 pp | +0.53 |
| Global Liquid Hydrogen Tank Market | A_no_playbook | 0.90 pp | n/a | n/a | 11.48 pp | 1.96 pp | -1.00 |
| Global Liquid Hydrogen Tank Market | B_playbook | 1.20 pp | n/a | n/a | 4.21 pp | 1.24 pp | +0.40 |
| Global Liquid Hydrogen Tank Market | C_playbook_calibrated | 0.00 pp | n/a | n/a | 9.58 pp | 1.19 pp | +0.40 |
| India Mobile Phone Accessories Market | A_no_playbook | 3.69 pp | 9.10 pp | 1.73 pp | 10.73 pp | 2.48 pp | -0.80 |
| India Mobile Phone Accessories Market | B_playbook | 3.39 pp | 11.60 pp | 0.65 pp | 11.13 pp | 1.52 pp | -0.57 |
| India Mobile Phone Accessories Market | C_playbook_calibrated | 3.69 pp | 10.10 pp | 1.65 pp | 9.25 pp | 2.17 pp | -0.77 |
| U.S., Mexico, & Brazil Resin Compound Market | A_no_playbook | 0.29 pp | n/a | n/a | 3.92 pp | 0.62 pp | +0.79 |
| U.S., Mexico, & Brazil Resin Compound Market | B_playbook | 0.79 pp | n/a | n/a | 3.20 pp | 0.71 pp | +0.61 |
| U.S., Mexico, & Brazil Resin Compound Market | C_playbook_calibrated | 1.79 pp | n/a | n/a | 3.32 pp | 0.54 pp | +0.93 |
| U.S. Digital Signage Market | A_no_playbook | 0.29 pp | n/a | n/a | 6.80 pp | 0.82 pp | +0.83 |
| U.S. Digital Signage Market | B_playbook | 0.59 pp | n/a | n/a | 5.12 pp | 1.14 pp | +0.92 |
| U.S. Digital Signage Market | C_playbook_calibrated | 0.39 pp | n/a | n/a | 5.90 pp | 1.03 pp | +0.81 |
| Global Well Planning & Engineering Software Market | A_no_playbook | 1.15 pp | 4.43 pp | 0.41 pp | 8.78 pp | 1.17 pp | +0.46 |
| Global Well Planning & Engineering Software Market | B_playbook | 2.55 pp | 2.72 pp | 0.29 pp | 8.34 pp | 0.84 pp | +0.44 |
| Global Well Planning & Engineering Software Market | C_playbook_calibrated | 2.15 pp | 3.02 pp | 0.76 pp | 6.51 pp | 0.90 pp | +0.56 |
| Global Industrial High Speed Helical Gearbox After | A_no_playbook | 0.92 pp | 3.19 pp | 1.07 pp | 7.12 pp | 1.13 pp | -0.23 |
| Global Industrial High Speed Helical Gearbox After | B_playbook | 1.22 pp | 7.00 pp | 1.00 pp | 6.88 pp | 0.94 pp | +0.06 |
| Global Industrial High Speed Helical Gearbox After | C_playbook_calibrated | 1.42 pp | 5.00 pp | 0.94 pp | 6.33 pp | 1.04 pp | +0.06 |