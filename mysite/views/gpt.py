from openai import OpenAI
import os

client = OpenAI()
client.api_key = os.getenv("OPENAI_API_KEY")

# gpt-4-1106-preview 128k
# gpt-4-vision-preview 128k
# gpt-4-32k -> gpt-4-32k-0613 32k
# gpt-4 ->gpt-4-0613 8k

# gpt-3.5-turbo-1106 16k
# gpt-3.5-turbo gpt-3.5-turbo-0613. 4k
# gpt-3.5-turbo-16k gpt-3.5-turbo-0613. 16k
# gpt-3.5-turbo-instruct 4k

# babbage-002
# davinci-002

system = "You are a helpful assistant specialized in real estate. Your goal is to help managers and tenants answer their questions"


def askGPT(message, system=system, context=None):
    messages = [{"role": "system", "content": system}]

    if context:
        messages.append({"role": "assistant", "content": context})

    messages.append({"role": "user", "content": message})

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=messages
    )

    return response.choices[0].message['content']
