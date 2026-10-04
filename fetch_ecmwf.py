from pathlib import Path
from datetime import datetime, timedelta, timezone
import json

from ecmwf.opendata import Client
from eccodes import (
    codes_grib_new_from_file,
    codes_get,
    codes_grib_find_nearest,
    codes_release,
)


# ---------------------------------------------------------
# Iceland locations used by Vera Island
# ---------------------------------------------------------

LOCATIONS = {
    "reykjavik": {
        "name": "Reykjavík",
        "lat": 64.1466,
        "lon": -21.9426,
    },
    "snaefellsnes": {
        "name": "Snæfellsnes",
        "lat": 64.9000,
        "lon": -23.8000,
    },
    "vik": {
        "name": "Vík",
        "lat": 63.4194,
        "lon": -19.0097,
    },
    "hofn": {
        "name": "Höfn",
        "lat": 64.2539,
        "lon": -15.2082,
    },
    "akureyri": {
        "name": "Akureyri",
        "lat": 65.6885,
        "lon": -18.1262,
    },
    "egilsstadir": {
        "name": "Egilsstaðir",
        "lat": 65.2669,
        "lon": -14.3948,
    },
}


# 0 to 336 hours = 14 days
# AIFS provides one point every 6 hours
FORECAST_STEPS = list(range(0, 337, 6))


def latest_complete_aifs_run():
    """
    Find the latest AIFS model run that should already have
    the complete 14-day forecast available.

    ECMWF runs AIFS at:
    00, 06, 12, 18 UTC

    We conservatively wait 7h45 after the model start time
    so the long-range steps are available.
    """

    now = datetime.now(timezone.utc)

    candidates = []

    for days_back in range(0, 3):

        day = (now - timedelta(days=days_back)).date()

        for hour in (0, 6, 12, 18):

            run = datetime(
                day.year,
                day.month,
                day.day,
                hour,
                0,
                tzinfo=timezone.utc,
            )

            ready = run + timedelta(hours=7, minutes=45)

            if ready <= now:
                candidates.append(run)

    if not candidates:
        raise RuntimeError("Could not determine a complete ECMWF AIFS run.")

    return max(candidates)


def iso_utc(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return (
        dt.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def main():

    print("Starting Vera Island ECMWF cloud forecast update...")

    model_run = latest_complete_aifs_run()

    print(
        "Using ECMWF AIFS run:",
        model_run.strftime("%Y-%m-%d %H:%M UTC"),
    )


    # -----------------------------------------------------
    # Prepare paths
    # -----------------------------------------------------

    data_directory = Path("data")
    data_directory.mkdir(exist_ok=True)

    grib_file = Path("aifs_tcc.grib2")

    output_file = data_directory / "ecmwf_cloud.json"


    # -----------------------------------------------------
    # Download ECMWF AIFS Total Cloud Cover
    # -----------------------------------------------------

    client = Client(
        source="ecmwf",
        model="aifs-single",
    )

    print("Downloading ECMWF Total Cloud Cover...")

    client.retrieve(
        date=model_run.strftime("%Y-%m-%d"),
        time=model_run.hour,
        stream="oper",
        type="fc",
        param="tcc",
        step=FORECAST_STEPS,
        target=str(grib_file),
    )

    print("Download completed.")


    # -----------------------------------------------------
    # Read GRIB2 data
    # -----------------------------------------------------

    forecasts = []

    with grib_file.open("rb") as file:

        while True:

            gid = codes_grib_new_from_file(file)

            if gid is None:
                break

            try:

                parameter = codes_get(gid, "shortName")

                if parameter != "tcc":
                    continue

                step_hours = int(
                    codes_get(gid, "endStep")
                )

                validity_date = int(
                    codes_get(gid, "validityDate")
                )

                validity_time = int(
                    codes_get(gid, "validityTime")
                )

                valid_datetime = datetime.strptime(
                    f"{validity_date:08d}{validity_time:04d}",
                    "%Y%m%d%H%M",
                ).replace(tzinfo=timezone.utc)


                cloud_values = {}


                # -----------------------------------------
                # Extract cloud cover for each Iceland area
                # -----------------------------------------

                for location_id, location in LOCATIONS.items():

                    nearest = codes_grib_find_nearest(
                        gid,
                        location["lat"],
                        location["lon"],
                    )[0]

                    value = float(nearest.value)


                    # ECMWF TCC is normally stored 0–1.
                    # Convert to percentage.
                    if value <= 1.5:
                        value = value * 100


                    value = max(
                        0.0,
                        min(
                            100.0,
                            value
                        )
                    )


                    cloud_values[location_id] = round(
                        value,
                        1
                    )


                forecasts.append(
                    {
                        "valid_time": iso_utc(
                            valid_datetime
                        ),
                        "step_hours": step_hours,
                        "cloud_total_percent": cloud_values,
                    }
                )


            finally:

                codes_release(gid)


    forecasts.sort(
        key=lambda item: item["valid_time"]
    )


    # -----------------------------------------------------
    # Create JSON result
    # -----------------------------------------------------

    result = {

        "source": "ECMWF",

        "model": "AIFS Single",

        "parameter": "Total Cloud Cover",

        "model_run": iso_utc(model_run),

        "generated_at": iso_utc(
            datetime.now(timezone.utc)
        ),

        "forecast_interval_hours": 6,

        "forecast_length_hours": 336,

        "locations": LOCATIONS,

        "forecasts": forecasts,

        "attribution": (
            "Weather data: ECMWF Open Data"
        ),
    }


    with output_file.open(
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            result,
            file,
            ensure_ascii=False,
            indent=2,
        )


    print(
        f"JSON created successfully: {output_file}"
    )

    print(
        f"Forecast points: {len(forecasts)}"
    )


    # Remove temporary GRIB file
    if grib_file.exists():
        grib_file.unlink()


    print("Vera Island cloud update finished.")


if __name__ == "__main__":
    main()
