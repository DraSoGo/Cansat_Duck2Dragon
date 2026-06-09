# Rocket/CanSat Landing Prediction Simulation - Design Specification

**Date:** 2026-06-09  
**Project:** CANSAT Duck2Dragon  
**Output:** `Data/landing_prediction.ipynb`

---

## 1. Purpose

Pre-flight planning tool to predict rocket and CanSat landing locations given:
- Launch site coordinates
- Weather conditions (wind profile by altitude)
- Rocket specifications from OpenRocket .ork file
- Deployment timing/uncertainty parameters

Primary use: recovery logistics planning. Secondary: post-flight validation against telemetry.

---

## 2. Architecture

### 2.1 Pipeline Overview

```
User Input → .ork Parser → RocketPy Setup → Monte Carlo Engine → Analysis → Visualization
```

**Components:**
1. **Configuration** - launch site, wind layers, uncertainty parameters
2. **Rocket Definition** - parse .ork, create RocketPy Motor/Rocket objects
3. **Environment** - ISA atmosphere + altitude-dependent wind profile
4. **Simulation Engine** - RocketPy Flight wrapper with parachute triggers
5. **Monte Carlo Runner** - parallel execution, parameter perturbation
6. **Post-Processor** - extract landing coordinates, compute statistics
7. **Visualizer** - 2D map (folium), 3D trajectory (matplotlib), interactive widgets (ipywidgets)

### 2.2 Separation Model

Two independent trajectories:
- **Rocket body:** Main parachute deploys at apogee
- **CanSat payload:** Separate Flight object, different mass/drag/parachute

Instant separation at apogee (deployment altitude configurable).

---

## 3. Physics Model

### 3.1 RocketPy 6-DOF Simulation

RocketPy handles:
- Thrust curve integration
- Aerodynamic forces (drag via barrowman coefficients from .ork)
- Gravity variation with altitude
- Atmospheric density (ISA model)
- Wind drift (logarithmic profile per altitude)
- Parachute drag (Cd = 1.5 for hemispherical chute)
- Rail departure dynamics

### 3.2 Coordinate Systems

- **Launch frame:** North-East-Down (NED) relative to launch site
- **Geodetic:** WGS84 lat/lon/altitude
- **Body frame:** Roll-pitch-yaw from quaternion (for attitude visualization)

### 3.3 Wind Profile

Logarithmic wind model (from PDF research):
```
V_wind(z) = (u_*/κ) * ln(z/z0)
```
Where:
- `u_*` = friction velocity (derived from surface wind)
- `κ` = von Kármán constant (0.4)
- `z0` = roughness length (terrain dependent: 0.001 for water, 0.01 for grass, 0.5 for urban)

User inputs wind as discrete layers (altitude, speed, direction). RocketPy interpolates.

### 3.4 Atmospheric Model

ISA (International Standard Atmosphere):
- Sea level: ρ₀ = 1.225 kg/m³, T₀ = 288.15 K, P₀ = 101325 Pa
- Lapse rate: -6.5 K/km (troposphere)
- Density variation: ρ(z) = ρ₀ * e^(-z/H) where H = 8500 m

---

## 4. Monte Carlo Uncertainty

### 4.1 Varied Parameters

**Wind:**
- Speed: ±20% Gaussian noise per layer
- Direction: ±15° uniform random

**Parachute Deployment:**
- Apogee detection delay: 0-2s uniform
- Altitude uncertainty: ±5m Gaussian

**Drag Coefficient:**
- Body Cd: ±10% variation
- Parachute Cd: ±5% variation

**Launch Angle:**
- Rail tilt: ±2° from vertical (azimuth ±5°)

**Atmospheric Conditions:**
- Pressure: ±5 hPa
- Temperature: ±3°C

### 4.2 Sampling Strategy

Latin Hypercube Sampling (LHS) for better coverage with fewer samples than pure random. User configurable: 100 (fast), 500 (default), 1000+ (production).

### 4.3 Parallel Execution

Use `joblib.Parallel` with `n_jobs=-1` (all cores). Progress bar via `tqdm`.

---

## 5. Data Flow

### 5.1 Input Structure

```python
config = {
    'launch_site': {'lat': 13.7563, 'lon': 100.5018, 'elevation': 10},  # Bangkok example
    'wind_layers': [
        {'altitude': 0, 'speed': 3, 'direction': 45},      # m, m/s, degrees
        {'altitude': 100, 'speed': 5, 'direction': 50},
        {'altitude': 500, 'speed': 8, 'direction': 60},
    ],
    'roughness_length': 0.01,  # grass/farmland
    'ork_file': 'rocket_design.ork',
    'cansat_mass': 0.300,  # kg
    'cansat_parachute_diameter': 0.5,  # m
    'deployment_altitude': 100,  # m AGL for CanSat separation
    'monte_carlo_samples': 500,
    'random_seed': 42,
}
```

### 5.2 Output Structure

```python
results = {
    'rocket_landings': [(lat1, lon1), (lat2, lon2), ...],  # n_samples coordinates
    'cansat_landings': [(lat1, lon1), (lat2, lon2), ...],
    'rocket_trajectories': [traj1, traj2, ...],  # subset for 3D viz
    'cansat_trajectories': [traj1, traj2, ...],
    'statistics': {
        'rocket': {'mean': (lat, lon), 'std': (σ_lat, σ_lon), 'cep95': distance_m},
        'cansat': {'mean': (lat, lon), 'std': (σ_lat, σ_lon), 'cep95': distance_m},
    },
    'parameters_used': config,
}
```

### 5.3 Trajectory Format

```python
trajectory = {
    'time': [0, 0.1, 0.2, ...],  # seconds
    'x': [...],  # meters East
    'y': [...],  # meters North
    'z': [...],  # meters altitude AGL
    'vx': [...], 'vy': [...], 'vz': [...],  # m/s
    'lat': [...], 'lon': [...],  # geodetic
    'events': {'apogee': {'time': 12.3, 'altitude': 245.6}, 'landing': {...}},
}
```

---

## 6. Implementation Details

### 6.1 Dependencies

```python
rocketpy>=1.2.0  # Core simulation
numpy>=1.24
scipy>=1.10
pandas>=2.0
matplotlib>=3.7
folium>=0.14  # 2D map
ipywidgets>=8.0  # Interactive sliders
tqdm>=4.65  # Progress bars
joblib>=1.3  # Parallel processing
plotly>=5.14  # 3D interactive alternative
```

### 6.2 Notebook Structure

**Cells:**
1. **Title + Overview** - Markdown: project intro, physics summary
2. **Imports** - All dependencies
3. **Configuration** - Interactive widgets for launch site, wind, parameters
4. **Physics Background** - Markdown: equations from PDF (thrust, drag, wind profile, ISA)
5. **OpenRocket Parser** - Load .ork, extract motor/geometry/mass
6. **RocketPy Setup** - Environment, Motor, Rocket, Flight objects
7. **Single Flight Demo** - Run one simulation, plot trajectory
8. **Monte Carlo Engine** - Parallel loop with parameter sampling
9. **Results Analysis** - Statistics, landing ellipses (CEP95)
10. **2D Visualization** - Folium map with launch site, mean landing, scatter
11. **3D Visualization** - Matplotlib 3D trajectories (subset)
12. **Interactive Dashboard** - ipywidgets sliders: vary wind/deployment, see update
13. **Comparison with Telemetry** - Load log.txt, overlay actual trajectory
14. **Export** - Save results to CSV/JSON for GIS tools

### 6.3 Key Functions

```python
def parse_ork(filepath: str) -> dict:
    """Extract motor, mass, dimensions, Cd from OpenRocket XML."""
    
def create_rocketpy_environment(launch_site: dict, wind_layers: list, roughness: float) -> rocketpy.Environment:
    """Build Environment with wind profile and ISA atmosphere."""
    
def create_rocket(ork_data: dict, env: rocketpy.Environment) -> rocketpy.Rocket:
    """Instantiate Rocket with motor, parachutes, mass from .ork."""
    
def run_single_flight(rocket: rocketpy.Rocket, env: rocketpy.Environment, params: dict) -> dict:
    """Execute one Flight, return trajectory + landing coords."""
    
def monte_carlo_simulation(rocket: rocketpy.Rocket, env: rocketpy.Environment, config: dict, n_samples: int) -> dict:
    """Parallel Monte Carlo with parameter perturbation. Returns results dict."""
    
def compute_cep95(landings: list) -> float:
    """95% Circular Error Probable - radius containing 95% of landings."""
    
def plot_2d_map(results: dict, launch_site: dict) -> folium.Map:
    """Folium map with scatter, ellipse, launch marker."""
    
def plot_3d_trajectories(trajectories: list, n_plot: int = 20):
    """Matplotlib 3D: subset of trajectories for clarity."""
    
def create_interactive_dashboard(base_config: dict) -> ipywidgets.VBox:
    """Sliders for wind/deployment → real-time prediction update."""
```

### 6.4 Educational Content

**Markdown cells explain:**
- Equation of motion: F_total = T + D + W = m*a
- Drag force: D = 0.5 * ρ * v² * Cd * A
- Terminal velocity: v_term = sqrt(2*m*g / (ρ*Cd*A))
- Wind drift: horizontal displacement = ∫ V_wind(z) dt during descent
- ISA model: pressure/density/temperature variation
- Logarithmic wind profile derivation
- Monte Carlo rationale: why LHS vs random
- CEP95 definition: recovery planning metric

Each equation includes:
- LaTeX rendering
- Variable definitions + units
- Numerical example
- Link to PDF reference page

---

## 7. Validation

### 7.1 Unit Tests (inline cells)

- Parser: verify .ork XML extraction matches known values
- Wind profile: check logarithmic interpolation at test altitudes
- Coordinate conversion: round-trip lat/lon ↔ NED
- CEP95: synthetic Gaussian distribution → known radius

### 7.2 Sanity Checks

- Apogee altitude matches ballistic estimate (no wind case)
- Landing distance increases linearly with wind speed (low wind regime)
- Heavier CanSat drifts less than rocket body
- Zero wind → landing at launch site (within numerical error)

### 7.3 Telemetry Comparison

Load `log.txt` from ground station:
- Extract GPS trajectory (lat, lon, alt)
- Overlay on 3D plot
- Compute RMSE between predicted and actual
- Identify discrepancies (actual wind vs forecast, deployment timing)

---

## 8. Performance Targets

- Single flight: <1 second
- 500 Monte Carlo runs: <30 seconds (8-core CPU)
- 1000 runs: <60 seconds
- Interactive slider update: <2 seconds (100 samples)
- Notebook total runtime: <5 minutes (all cells)

Memory: <2GB peak (trajectory downsampling for storage).

---

## 9. Future Enhancements (Out of Scope)

- Weather API integration (GFS/NOAA forecast fetch)
- Multi-stage rockets (booster separation)
- Real-time prediction during flight (telemetry stream → updated forecast)
- Machine learning surrogate (train on simulations, fast inference)
- Terrain elevation (landing on hills/mountains)
- Export to Google Earth KML

---

## 10. Assumptions and Limitations

**Assumptions:**
- Flat Earth approximation (valid for <10 km range)
- No thermal updrafts/turbulence (wind is steady)
- Parachute fully inflates instantly
- No parachute oscillation/spin
- Rail perfectly vertical (±2° only in Monte Carlo)
- ISA atmosphere (no weather fronts)

**Limitations:**
- RocketPy 6-DOF may be overkill for small rockets (but accurate)
- Wind profile assumes neutral atmospheric stability
- Roughness length manually selected (no terrain database)
- CanSat separation simplified (no ejection velocity)

**Validity Range:**
- Altitudes: 0-3000m AGL
- Wind speeds: 0-15 m/s
- Rocket mass: 0.5-5 kg
- CanSat mass: 0.1-0.5 kg
- Launch angles: within 5° of vertical

---

## 11. Deliverable Checklist

- [ ] Jupyter notebook `Data/landing_prediction.ipynb`
- [ ] All cells runnable top-to-bottom without errors
- [ ] Interactive widgets functional (ipywidgets + matplotlib)
- [ ] 2D map displays correctly (folium)
- [ ] 3D trajectory plots render (matplotlib)
- [ ] Monte Carlo completes in <60s for 500 samples
- [ ] Educational markdown cells complete with equations
- [ ] Example .ork file included or path documented
- [ ] Sample wind profile provided
- [ ] Telemetry comparison cell functional with `log.txt`
- [ ] README section added explaining how to run notebook
- [ ] Dependencies installable via `pip install -r requirements.txt`

---

**End of Design Specification**
