from fastapi import FastAPI, Request, Response, Depends
from diffusers import StableDiffusionPipeline, EulerAncestralDiscreteScheduler
import bittensor as bt
import torch
from typing import List
from utils import base64_to_pil_image, instantiate_from_config
from matching_hash import matching_images
from pydantic import BaseModel
import uvicorn
import argparse
import time
import os
import threading
from slowapi.errors import RateLimitExceeded
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from models import *
import yaml
import argparse

MODEL_CONFIG = yaml.load(open("model_config.yaml"), yaml.FullLoader)


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=10002)
    parser.add_argument("--netuid", type=str, default=1)
    parser.add_argument("--min_stake", type=int, default=100)
    parser.add_argument(
        "--chain_endpoint",
        type=str,
        default="subtensor_fixed_imagenet.thinkiftechnology.com:9944",
    )
    parser.add_argument(
        "--model_name",
        type=str,
        choices=list(MODEL_CONFIG.keys()),
    )
    args = parser.parse_args()
    return args


class Prompt(BaseModel):
    prompt: str
    seed: int
    images: List[List[str]]
    model_name: str
    additional_params: dict = {}


app = FastAPI()
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

ARGS = get_args()
MODEL = instantiate_from_config(MODEL_CONFIG[ARGS.model_name])


@app.middleware("http")
@limiter.limit("30/minute")
async def filter_allowed_ips(request: Request, call_next):
    print(str(request.url))
    if (request.client.host not in ALLOWED_IPS) and (
        request.client.host != "127.0.0.1"
    ):
        print(f"Blocking an unallowed ip:", request.client.host, flush=True)
        return Response(
            content="You do not have permission to access this resource",
            status_code=403,
        )
    print(f"Allow an ip:", request.client.host, flush=True)
    response = await call_next(request)
    return response


@app.post("/verify")
async def get_rewards(data: Prompt):
    generator = torch.Generator().manual_seed(data.seed)
    validator_images = MODEL(
        prompt=data.prompt, generator=generator, **data.additional_params
    ).images
    rewards = []
    for miner_images in data.images:
        try:
            miner_images = [base64_to_pil_image(image) for image in miner_images]
            reward = matching_images(miner_images, validator_images)
            print("Verify Result:", reward, flush=True)
        except Exception as e:
            print(e, flush=True)
            reward = 0
        rewards.append(reward)
    return {"rewards": rewards}


def define_allowed_ips(url, netuid, min_stake):
    global ALLOWED_IPS
    ALLOWED_IPS = []
    while True:
        all_allowed_ips = []
        subtensor = bt.subtensor(url)
        metagraph = subtensor.metagraph(netuid)
        for uid in range(len(metagraph.total_stake)):
            if metagraph.total_stake[uid] > min_stake:
                all_allowed_ips.append(metagraph.axons[uid].ip)
        ALLOWED_IPS = all_allowed_ips
        print("Updated allowed ips:", ALLOWED_IPS, flush=True)
        time.sleep(60)


if __name__ == "__main__":
    allowed_ips_thread = threading.Thread(
        target=define_allowed_ips,
        args=(
            ARGS.chain_endpoint,
            ARGS.netuid,
            ARGS.min_stake,
        ),
    )
    allowed_ips_thread.setDaemon(True)
    allowed_ips_thread.start()
    uvicorn.run(app, host="0.0.0.0", port=ARGS.port)
