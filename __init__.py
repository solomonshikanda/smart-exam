import subprocess
import os

def download_with_idm(url, save_path, filename):
    idm_path = r"C:\Program Files (x86)\Internet Download Manager\IDMan.exe"

    if not os.path.exists(idm_path):
        raise FileNotFoundError("IDMan.exe not found. Check your IDM installation path.")

    command = [
        idm_path,
        "/d", url,
        "/p", save_path,
        "/f", filename,
        "/q", "/n"
    ]

    subprocess.run(command)

# Destination folder
save_folder = r"G:\flightdatazipped"

# Ensure the folder exists
os.makedirs(save_folder, exist_ok=True)

# Loop through years and months
for year in range(2014, 2025):  # 2014 to 2024 inclusive
    for month in range(1, 13):  # January to December
        # Skip months beyond Dec 2024
        if year == 2024 and month > 12:
            break

        # Format parts
        ym = f"{year}_{month}"
        filename = f"On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{ym}.zip"
        url = f"https://www.transtats.bts.gov/PREZIP/{filename}"

        print(f"Queuing download: {filename}")
        download_with_idm(url, save_folder, filename)
