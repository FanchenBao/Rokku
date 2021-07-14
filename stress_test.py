from src.pi_to_pi import subscriber
from multiprocessing import Queue, Process
import multiprocessing
from time import sleep, time
import subprocess
from pathlib import Path
import re
import logging
import yaml
import csv

ROOT = Path('.').absolute().parent
EMIT = ROOT.joinpath('probe_request_injection/emit/emit.sh')
EMITTER_MAP = {
    '7d:fd:97': '06',
    '7d:fd:a6': '09',
    '85:7a:5e': '29',
    '85:78:a5': '30',
    '6e:4e:8a': '40',
}

# set up logger
with open('logger_config.yaml', "r") as f:
    config = yaml.safe_load(f.read())
    logging.config.dictConfig(config)
logger = logging.getLogger("StressTest")


def get_device_mac(interface='eth0') -> str:
    """Get MAC address of the given interface on the device.

    This piece of code is directly referenced from:
    https://www.raspberrypi-spy.co.uk/2012/06/finding-the-mac-address-of-a-raspberry-pi/

    If the interface exists, return its full MAC address as string; otherwise
    return all zeros.

    :param interface:   Web interface whose MAC address to be retrieved.
    :return: A string of MAC address of the given interface, or all zeros.
    """
    try:
        with open(f'/sys/class/net/{interface}/address') as file_obj:
            mac_addr = file_obj.read()
    except Exception:
        mac_addr = '00:00:00:00:00:00'
    mac_addr_len: int = 17
    return mac_addr[:mac_addr_len]


def stress_test(num_sensors: str, rd: str, power_range: int, duration: int) -> None:
    with open(f'stress_test_summary.csv', 'a', newline='') as csvfile:
        writer = csv.writer(csvfile)
        for power in range(power_range):
            emitter_id = EMITTER_MAP[get_device_mac()[9:]]
            mac = f'{num_sensors}{rd}:{emitter_id}:{power:02}'
            interval = 1 / (2**power)
            subprocess.run(f"{EMIT} -i wlan1 -c 10 --interval {interval} --mac {mac}", shell=True, text=True, capture_output=True)
            start_time = int(time() * 1000)
            sleep(duration + 1)
            subprocess.run('tmux send-keys -t emit C-C Enter', shell=True, text=True, capture_output=True)
            sleep(1)
            p = subprocess.run('tmux capture-pane -p', shell=True, text=True, capture_output=True)
            sleep(1)
            subprocess.run('tmux kill-server', shell=True, text=True, capture_output=True)
            match = re.search(r'Sent\s(\d+)\spackets', p.stdout)
            logger.info(f'Interval {interval} complete')
            writer.writerow(
                [num_sensors, rd, interval, emitter_id, match.group(1), start_time, int(time() * 1000)],
            )


if __name__ == '__main__':
    logger.info('Stress test ready. Listening to commands...')
    multiprocessing.set_start_method('fork')

    q = Queue()
    sub = subscriber.Subscriber(q, topic="lmrxwwnudcusvlsrelvh/experiment")
    child_proc = Process(target=sub.start_listen, args=())
    child_proc.start()

    with open(f'stress_test_summary.csv', 'a', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['num_sensors', 'round', 'interval', 'emitter_id', 'emit_count', 'start', 'end'])

    while True:
        if not q.empty():
            msg = q.get()
            if msg == 'exit':
                break
            try:
                num_sensors, rd, power_range, duration = msg.split('-')
            except Exception:
                logger.error('MQTT message wrong')
                break
            logger.info(f'Stress test START! number of sensors: {num_sensors}, round: {rd}, power_range: {power_range}, duration: {duration}')
            try:
                stress_test(num_sensors, rd, int(power_range), int(duration))
            except Exception as err:
                logger.error(f'Stress test FAILED. Error message: {err}')
                break
            logger.info('Stress test END!')
        sleep(1)
    
    # gracefully end all processes
    child_proc.terminate()
    child_proc.join()
    sub.close()

    
