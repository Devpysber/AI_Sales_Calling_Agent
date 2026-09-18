import sys, os
from dotenv import load_dotenv
load_dotenv()

from app.services.llm import stream
import logging
logging.basicConfig(level=logging.DEBUG)

messages = [{"role": "system", "content": "You have a tool to check weather. Use it."}, {"role": "user", "content": "What is the weather in Paris?"}]
tools = [{"type": "function", "function": {"name": "check_weather", "description": "Check weather in a city", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}]

for delta in stream(messages, tools=tools):
    print("DELTA:", delta)
