#!/usr/bin/env python3
"""Expose the SUT's RAPL energy counters over HTTP to a load driver on another machine.

measure.py reads energy from /sys/class/powercap on the machine it runs on. When the
load driver is a separate machine, as it should be, that machine's counters say nothing
about the SUT, so this server runs on the SUT instead and measure.py reads it with
--energy-url. Each request reads the counters once; the cost on the SUT is a handful of
HTTP requests per measured run, which is negligible against a window of minutes.

energy_uj is root-only on current kernels (PLATYPUS mitigation), hence sudo. Bind to the
direct link to the load driver, not to every interface, so the counters are not exposed
on other networks:

    sudo python3 rapl_server.py --bind 10.10.10.1 --port 9100

GET /energy returns the counters together with the SUT's state at the moment of reading.
It only reads sysfs files, so it is fast enough to bracket the measured window:
    {"host": {...},
     "state": {"power": {...}, "temperatures_c": {...}, "battery": {...}},
     "zones": {"package-0": {"energy_uj": int, "max_energy_range_uj": int}, ...}}

GET /containers returns the running containers and the image each one runs, so a run can
record exactly which build it measured. It shells out to docker and is slower, so
measure.py calls it once per run, outside the measured window:
    {"containers": [{"name": str, "id": str, "started_at": str, "image": str,
                     "image_id": str, "host_ports": [int], "catalina_opts": str}]}
"""

import argparse
import json
import os
import platform
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

RAPL_ROOT = Path("/sys/class/powercap")
POWER_SUPPLY_ROOT = Path("/sys/class/power_supply")
THERMAL_ROOT = Path("/sys/class/thermal")


def cpu_model():
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.partition(":")[2].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def machine_model():
    """The machine's model from DMI, e.g. "Lenovo ideapad 320-15IKB", or None without DMI.

    Recorded instead of the hostname, which on a personal machine often carries a person's
    name into data that is meant to be published. Lenovo puts the model in product_version
    and a type number in product_name; other vendors put the model in product_name.
    """
    dmi = Path("/sys/class/dmi/id")
    try:
        vendor = (dmi / "sys_vendor").read_text().strip()
        field = "product_version" if vendor.upper() == "LENOVO" else "product_name"
        return (dmi / field).read_text().strip() or None
    except OSError:
        return None


HOST = {"model": machine_model(), "system": platform.system(), "machine": platform.machine(),
        "cpus": os.cpu_count(), "cpu_model": cpu_model()}

# Settings that change package power for the same work. Read on every request rather than
# once, because they can change under a running server: turbo across a reboot, AC when the
# charger is pulled. Recording them in each run lets a mismatch be spotted afterwards.
POWER_STATE_FILES = {
    "no_turbo": "/sys/devices/system/cpu/intel_pstate/no_turbo",
    "governor": "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor",
    "energy_performance_preference":
        "/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference",
}


def read_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def power_supplies(kind):
    return [supply for supply in sorted(POWER_SUPPLY_ROOT.glob("*"))
            if read_text(supply / "type") == kind]


def power_state():
    state = {name: read_text(path) for name, path in POWER_STATE_FILES.items()}
    mains = power_supplies("Mains")
    state["ac_online"] = any(read_text(supply / "online") == "1" for supply in mains) if mains else None
    return state


def temperatures():
    """Every thermal zone in degrees Celsius. x86_pkg_temp is the CPU package.

    Taken at the start and end of each measured window, so thermal drift across a
    series shows up in the data instead of being assumed away.
    """
    temps = {}
    for zone in sorted(THERMAL_ROOT.glob("thermal_zone*")):
        kind, millidegrees = read_text(zone / "type"), read_text(zone / "temp")
        if kind and millidegrees and millidegrees.lstrip("-").isdigit():
            temps[kind] = int(millidegrees) / 1000.0
    return temps


def battery():
    """Charge and charging state. A charging battery warms the machine, and a laptop may
    limit power differently at low charge; neither shows in the package counters."""
    batteries = power_supplies("Battery")
    if not batteries:
        return None
    supply = batteries[0]
    capacity = read_text(supply / "capacity")
    return {"capacity_percent": int(capacity) if capacity and capacity.isdigit() else None,
            "status": read_text(supply / "status")}


def sut_state():
    return {"power": power_state(), "temperatures_c": temperatures(), "battery": battery()}


def read_zones():
    zones = {}
    for zone in sorted(RAPL_ROOT.glob("intel-rapl:*")):
        try:
            zones[(zone / "name").read_text().strip()] = {
                "energy_uj": int((zone / "energy_uj").read_text()),
                "max_energy_range_uj": int((zone / "max_energy_range_uj").read_text()),
            }
        except (OSError, ValueError):
            continue
    return zones


def containers():
    """Running containers with their image id and published host ports, or None if docker
    cannot be queried. The image id is what proves both configurations ran one build."""
    try:
        ids = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True,
                             check=True, timeout=10).stdout.split()
        if not ids:
            return []
        inspected = json.loads(subprocess.run(["docker", "inspect", *ids], capture_output=True,
                                              text=True, check=True, timeout=10).stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    result = []
    for container in inspected:
        ports = (container.get("NetworkSettings") or {}).get("Ports") or {}
        host_ports = sorted({int(binding["HostPort"]) for bindings in ports.values() if bindings
                             for binding in bindings if binding.get("HostPort")})
        env = dict(entry.partition("=")[::2] for entry in container["Config"].get("Env") or [])
        result.append({"name": container["Name"].lstrip("/"),
                       # Which JVM instance served the run: a recreated container is a new
                       # JVM, and runs of one configuration can differ between instances.
                       "id": container["Id"][:12],
                       "started_at": (container.get("State") or {}).get("StartedAt"),
                       "image": container["Config"]["Image"],
                       "image_id": container["Image"],
                       "host_ports": host_ports,
                       # Carries the cache flag; the image id alone cannot tell on from off.
                       "catalina_opts": env.get("CATALINA_OPTS")})
    return result


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        path = self.path.rstrip("/")
        if path == "/energy":
            zones = read_zones()
            if not zones:
                self.send_error(503, "no readable RAPL zones (not running as root?)")
                return
            self.send_json({"host": HOST, "state": sut_state(), "zones": zones})
        elif path == "/containers":
            running = containers()
            if running is None:
                self.send_error(503, "docker could not be queried")
                return
            self.send_json({"containers": running})
        else:
            self.send_error(404)

    def send_json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bind", required=True, help="address of the link to the load driver")
    parser.add_argument("--port", type=int, default=9100)
    args = parser.parse_args()

    zones = read_zones()
    if not zones:
        parser.error("no readable RAPL zones under /sys/class/powercap (run with sudo)")
    print(f"RAPL zones: {', '.join(zones)}")
    print(f"state: {json.dumps(sut_state())}")
    running = containers()
    print("containers: " + (", ".join(f"{c['name']} {c['image_id'][:19]}" for c in running)
                            if running else "none found (is the stack up?)"))
    print(f"serving http://{args.bind}:{args.port}/energy and /containers")
    HTTPServer((args.bind, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
