import os
import re
import time
import asyncio
import discord
from discord.ext import commands
from collections import defaultdict, deque
from openai import AsyncOpenAI

#THIS IS DEEPSEEK-V4-FLASH, BUT CAN SWITCH TO V4-PRO

#only last 12 messages on discord will be fed into the ai's memory
MEMORY_SIZE = 12

#the very much needed ai shit

#loads persona file
def load_persona(name, tier='flash'):
    suffix = '_prompt_pro.txt' if tier == 'pro' else '_prompt.txt'
    path = os.path.join(os.path.dirname(__file__), 'personalities', f'{name.lower()}{suffix}')
    if not os.path.exists(path):
        # fallback to base prompt if pro version doesn't exist yet
        path = os.path.join(os.path.dirname(__file__), 'personalities', f'{name.lower()}_prompt.txt')
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return f.read().strip()

#this command lists all personas
def list_personas():
    names = []
    persona_dir = os.path.join(os.path.dirname(__file__), 'personalities')
    if os.path.exists(persona_dir):
        for fname in os.listdir(persona_dir):
            # only show base prompts, not _pro variants
            if fname.endswith('_prompt.txt') and '_prompt_pro' not in fname:
                names.append(fname.replace('_prompt.txt', ''))
    return sorted(names)

#this splits messages into chunks if they are too long (optimization)
def split_msg(text, limit=2000):
    chunks = []
    while len(text) > limit:
        split_at = text.rfind('\n', 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip('\n')
    if text:
        chunks.append(text)
    return chunks

class AIRoleplay(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.client = AsyncOpenAI(
            api_key=os.getenv('DEEPSEEK_API'),
            base_url='https://api.deepseek.com'
        )
        
        self.allowed_users = set()
        for key in ('OWNER_ID', 'SIDE_OWNER_ID'):
            val = os.getenv(key)
            if val:
                try:
                    self.allowed_users.add(int(val))
                except ValueError:
                    pass

        self.memory = defaultdict(lambda: deque(maxlen=MEMORY_SIZE))
        self.active_personas = {}
        self.prompts = {}
        
        self.input_tokens = 0
        self.output_tokens = 0
        self.session_start = time.time()
        self.webhooks = {}
        self.active_model = 'deepseek-v4-flash'
        self.active_channels = set()

    #only accepts me and hizuki
    def is_allowed(self, user):
        return user.id in self.allowed_users

    #this loads the persona file
    def get_prompt(self, channel_id):
        tier = 'pro' if 'pro' in self.active_model else 'flash'
        cache_key = (channel_id, tier)
        if cache_key not in self.prompts:
            name = self.active_personas.get(channel_id, 'yuuka')
            prompt = load_persona(name, tier)
            if not prompt:
                prompt = load_persona('yuuka', tier) or ''
            self.prompts[cache_key] = prompt
        return self.prompts[cache_key]

    #this gets the channel webhook, creates one if it doesnt exist
    async def get_webhook(self, channel):
        if channel.id in self.webhooks:
            return self.webhooks[channel.id]
        try:
            whs = await channel.webhooks()
            wh = next((w for w in whs if w.name == 'YuukaAI'), None)
            if not wh:
                wh = await channel.create_webhook(name='YuukaAI')
            self.webhooks[channel.id] = wh
            return wh
        except discord.Forbidden:
            return None

    #this sends the message as the persona, with vision support (if images are present)
    async def send_as_char(self, message, text, persona):
        avatar_path = os.path.join(os.path.dirname(__file__), 'personalities', f'{persona.lower()}_avatar.png')
        avatar_bytes = None
        if os.path.exists(avatar_path):
            with open(avatar_path, 'rb') as f:
                avatar_bytes = f.read()

        wh = await self.get_webhook(message.channel)
        chunks = split_msg(text)
        
        if wh:
            display_name = persona.capitalize()
            for i, chunk in enumerate(chunks):
                kwargs = {'content': chunk, 'username': display_name, 'wait': True}
                if i == 0 and avatar_bytes:
                    kwargs['avatar'] = avatar_bytes
                await wh.send(**kwargs)
        else:
            first = True
            for chunk in chunks:
                if first:
                    await message.reply(chunk)
                    first = False
                else:
                    await message.channel.send(chunk)

    #this is the main listener, it handles all messages
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        mentioned = self.bot.user in message.mentions
        in_active = message.channel.id in self.active_channels

        # reject non-allowed users who actively ping the bot
        if mentioned and not self.is_allowed(message.author):
            await message.reply("You dont have access to AI features")
            return

        # determine if we should respond at all
        if not mentioned and not in_active:
            return

        # in active mode: skip commands and ignore non-allowed users silently
        if in_active and not mentioned:
            if not self.is_allowed(message.author):
                return
            if message.content.startswith('!'):
                return

        #this removes the mention from the message
        content = re.sub(r'<@!?' + str(self.bot.user.id) + r'>', '', message.content).strip()
        if not content:
            return

        cid = message.channel.id
        mem = self.memory[cid]
        persona = self.active_personas.get(cid, 'yuuka')
        prompt = self.get_prompt(cid)

        # VISION SUPPORT
        user_content = [{"type": "text", "text": f'{message.author.display_name}: {content}'}]
        has_images = False
        
        for att in message.attachments:
            if att.content_type and att.content_type.startswith('image/'):
                has_images = True
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": att.url}
                })

        # DeepSeek and older models prefer pure strings if there are no images
        final_content = user_content if has_images else user_content[0]["text"]
        mem.append({'role': 'user', 'content': final_content})
        
        api_msgs = [{'role': 'system', 'content': prompt}] + list(mem)
        model_to_use = self.active_model

        #VISION SUPPORT END

        #this sends the message to the ai and handles the response
        async with message.channel.typing():
            try:
                resp = await self.client.chat.completions.create(
                    model=model_to_use,
                    messages=api_msgs,
                    max_tokens=1024
                )
            except Exception as e:
                await message.reply(f'api error: {e}')
                mem.pop()
                return

        reply = resp.choices[0].message.content.strip()
        
        if resp.usage:
            self.input_tokens += resp.usage.prompt_tokens
            self.output_tokens += resp.usage.completion_tokens

        mem.append({'role': 'assistant', 'content': reply})

        # simple typing delay calculation for better rp
        words = len(reply.split())
        delay = max(1.5, min(words / 60 * 2, 5.0))
        await asyncio.sleep(delay)

        await self.send_as_char(message, reply, persona)

    #this command lists all the ai commands, not an actual ai command##
    @commands.command()
    async def ai(self, ctx):
        ai_commands = [f'!{cmd.name}' for cmd in self.get_commands()]
        await ctx.reply('\n'.join(ai_commands))
    ######################################################################

    #this sets the persona, only usable by me and hizu
    @commands.command()
    async def setpersona(self, ctx, name: str):
        if not self.is_allowed(ctx.author):
            return
            
        prompt = load_persona(name)
        if not prompt:
            avail = ', '.join(list_personas()) or 'none'
            return await ctx.reply(f'couldnt find {name}. available: {avail}')

        self.active_personas[ctx.channel.id] = name.lower()
        self.prompts.pop(ctx.channel.id, None)
        self.memory[ctx.channel.id].clear()
        await ctx.reply(f'switched to {name} and wiped memory')

    #this clears the memory of the ai, only usable by me and hizu
    @commands.command()
    async def clearchat(self, ctx):
        if not self.is_allowed(ctx.author):
            return
        self.memory[ctx.channel.id].clear()
        await ctx.reply('memory cleared')

    #this enables active chat, only usable by me and hizu
    @commands.command()
    async def activechat(self, ctx):
        if not self.is_allowed(ctx.author):
            return
        self.active_channels.add(ctx.channel.id)
        await ctx.reply('active chat enabled — no need to ping, just talk')

    #this disables active chat, only usable by me and hizu
    @commands.command()
    async def stopchat(self, ctx):
        if not self.is_allowed(ctx.author):
            return
        self.active_channels.discard(ctx.channel.id)
        await ctx.reply('active chat disabled')

    #this lists all the personas, only usable by me and hizu
    @commands.command()
    async def personas(self, ctx):
        if not self.is_allowed(ctx.author):
            return
        names = list_personas()
        current = self.active_personas.get(ctx.channel.id, 'yuuka')
        lines = [f'- {n} {"(active)" if n == current else ""}' for n in names]
        await ctx.reply('**personas:**\n' + '\n'.join(lines) if lines else 'none found')

    #this shows the current token usage, only usable by me and hizu
    @commands.command()
    async def aicost(self, ctx):
        if not self.is_allowed(ctx.author):
            return
            
        elapsed = time.time() - self.session_start
        hours, r = divmod(int(elapsed), 3600)
        mins, _ = divmod(r, 60)

        cost_in = self.input_tokens * (0.14 / 1_000_000)
        cost_out = self.output_tokens * (0.28 / 1_000_000)
        total = cost_in + cost_out

        embed = discord.Embed(title='ai usage', color=0x00ff00)
        embed.add_field(name='uptime', value=f'{hours}h {mins}m', inline=False)
        embed.add_field(name='tokens', value=f'in: {self.input_tokens:,}\nout: {self.output_tokens:,}', inline=True)
        embed.add_field(name='cost', value=f'~${total:.5f}', inline=True)
        await ctx.reply(embed=embed)

    #THIS IS WHERE TURBO COMMANDS LIVE IN
    @commands.command()
    async def turbo(self, ctx):
        if not self.is_allowed(ctx.author):
            return
        if self.active_model == 'deepseek-v4-flash':
            self.active_model = 'deepseek-v4-pro'
            self.prompts.clear()
            await ctx.reply('switched to **deepseek-v4-pro**')
        else:
            self.active_model = 'deepseek-v4-flash'
            self.prompts.clear()
            await ctx.reply('switched to **deepseek-v4-flash**')

    #this shows the current model, only usable by me and hizu
    @commands.command()
    async def current(self, ctx):
        if not self.is_allowed(ctx.author):
            return
        await ctx.reply(f'currently using: **{self.active_model}**')

async def setup(bot):
    await bot.add_cog(AIRoleplay(bot))
