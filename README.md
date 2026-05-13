# Indoor Trainer

A small local macOS app that connects to a **Decathlon Van Rysel D500** indoor
trainer over Bluetooth (FTMS), drives it in **ERG mode** through a workout you
build yourself, optionally records HR from a **Garmin watch** broadcasting over
BLE, and exports a **.fit** file you can drag into Garmin Connect / Strava.

No accounts, no cloud — everything is on your Mac.

## Run

```bash
cd indoor-trainer
uv sync       # first time
uv run trainer
```

On first launch macOS will ask for **Bluetooth** permission for the Python
process — allow it (System Settings → Privacy & Security → Bluetooth).

## Workflow

1. **Library tab**: pick a workout (a few examples are seeded) or click **New…** to build one.
2. Click **Start ▶** to open the ride view.
3. Click **Connect trainer…** and pick your D500. (Make sure no other app — Zwift, MyHomeTrainer — is connected to it.)
4. Click **Connect HR…** and pick your Garmin. On the watch:
   *Settings → Sensors & Accessories → Wrist Heart Rate → Broadcast Heart Rate → Broadcast Now*. A chest strap also works.
5. Click **Start ▶** in the ride view. The trainer will follow each step's target wattage.
6. **Pause / Skip step / Finish** as needed. **Finish** writes a FIT file to `rides/`.
7. Drag the `.fit` into Garmin Connect (Activities → Upload) or Strava (Upload → File).

## Workout format

Each workout is a JSON file in `workouts/`. Step kinds:

- `steady` — hold `target_w` watts for `duration_s` seconds
- `ramp`   — linearly interpolate from `ramp_from_w` → `ramp_to_w`
- `free`   — no ERG target (still records data)

You can edit the JSON files directly or use the in-app builder.

## Files

- `workouts/*.json` — your workout library
- `rides/*.fit`     — exported activities (upload these)

## Notes

- Speed and distance are taken straight from the trainer's FTMS Indoor Bike
  Data, treated as a flat road. Real-world feel will depend on your trainer's
  power-to-speed model — but the watts and HR are real, and that's what
  matters for training.
- ERG only by design.
