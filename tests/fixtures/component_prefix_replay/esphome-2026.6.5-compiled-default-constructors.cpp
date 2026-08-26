// Syntax fixture extracted 2026-08-26 from the local throwaway ESPHome 2026.6.5 build at:
// /workspace/verdify-platform/repo/firmware/.esphome/build/greenhouse/src/main.cpp
// Observed full main.cpp SHA-256:
// a7dc1fc837761ef21da08d9969f185c84cf4a0252a498e6e798cbf5c29cfbfdf
// That local file is not an immutable archive or qualification receipt. This
// fixture proves parser spelling only. Qualification still requires the exact
// candidate build's generated main.cpp and firmware binary as mandatory input.
  new(band_track_fraction) globals::GlobalsComponent<float>(0.0);
  new(cold_vent_guard_delta_f) globals::GlobalsComponent<float>(10.0);
  new(cool_exit_hysteresis_f) globals::GlobalsComponent<float>(1.5);
  new(cool_stage2_exit_hysteresis_f) globals::GlobalsComponent<float>(1.0);
  new(cool_stage2_over_high_f) globals::GlobalsComponent<float>(1.0);
  new(direct_wet_stress_min_dew_margin_f) globals::GlobalsComponent<float>(8.0);
  new(direct_wet_stress_vpd_margin_kpa) globals::GlobalsComponent<float>(0.05);
  new(dwell_gate_ms) globals::GlobalsComponent<int>(300000);
  new(enthalpy_close_kjkg) globals::GlobalsComponent<float>(1.0);
  new(enthalpy_open_kjkg) globals::GlobalsComponent<float>(-2.0);
  new(fog_escalation_kpa) globals::GlobalsComponent<float>(0.4);
  new(heat_hysteresis_f) globals::GlobalsComponent<float>(1.0);
  new(min_fan_off_s) globals::GlobalsComponent<int>(90);
  new(min_fan_on_s) globals::GlobalsComponent<int>(120);
  new(min_fog_off_s) globals::GlobalsComponent<int>(60);
  new(min_fog_on_s) globals::GlobalsComponent<int>(60);
  new(min_heat_off_s) globals::GlobalsComponent<int>(180);
  new(min_heat_on_s) globals::GlobalsComponent<int>(120);
  new(min_vent_off_s) globals::GlobalsComponent<int>(60);
  new(min_vent_on_s) globals::GlobalsComponent<int>(60);
  new(mist_backoff_s) globals::GlobalsComponent<int>(600);
  new(mist_max_closed_vent_s) globals::GlobalsComponent<int>(600);
  new(mist_thermal_relief_s) globals::GlobalsComponent<int>(90);
  new(mister_all_delay_s) globals::GlobalsComponent<int>(300);
  new(mister_all_kpa) globals::GlobalsComponent<float>(1.9);
  new(mister_center_penalty) globals::GlobalsComponent<float>(0.5);
  new(mister_engage_delay_s) globals::GlobalsComponent<int>(45);
  new(mister_engage_kpa) globals::GlobalsComponent<float>(1.6);
  new(mister_min_off_s) globals::GlobalsComponent<int>(45);
  new(mister_pulse_gap_s) globals::GlobalsComponent<int>(45);
  new(mister_pulse_on_s) globals::GlobalsComponent<int>(60);
  new(mister_vpd_weight) globals::GlobalsComponent<float>(1.5);
  new(mister_water_budget_gal) globals::GlobalsComponent<float>(300.0);
  new(night_vpd_bias_kpa) globals::GlobalsComponent<float>(0.0);
  new(outdoor_staleness_max_s) globals::GlobalsComponent<int>(600);
  new(cool_all_fans_at_high_enabled) globals::GlobalsComponent<bool>(false);
  new(direct_wet_gate_enabled) globals::GlobalsComponent<bool>(true);
  new(direct_wet_stress_override_enabled) globals::GlobalsComponent<bool>(false);
  new(sw_dwell_gate_enabled) globals::GlobalsComponent<bool>(false);
  new(fog_closes_vent) globals::GlobalsComponent<bool>(true);
  new(mister_closes_vent) globals::GlobalsComponent<bool>(false);
  new(sw_summer_vent_enabled) globals::GlobalsComponent<bool>(true);
  new(hyst_temp_f) globals::GlobalsComponent<float>(1.0);
  new(vent_exchange_fraction) globals::GlobalsComponent<float>(0.30);
  new(vent_prefer_dp_delta_f) globals::GlobalsComponent<float>(5.0);
  new(vent_prefer_temp_delta_f) globals::GlobalsComponent<float>(5.0);
  new(hyst_vpd_kpa) globals::GlobalsComponent<float>(0.30);
  new(vpd_watch_dwell_s) globals::GlobalsComponent<int>(60);
