# ==========================================
# IMPORTS & CONSTANTS
# ==========================================
import os
import re
import time
import asyncio
import discord
from discord.ext import commands
from collections import defaultdict, deque
# pyrefly: ignore [missing-import]
from openai import AsyncOpenAI

#only last 12 messages on discord will be fed into the ai's memory
MEMORY_SIZE = 12

# These are the ONLY two valid DeepSeek V4 model IDs. deepseek-chat and
# deepseek-reasoner are legacy aliases scheduled for retirement on
# 2026/07/24 and are never used in this file.
MODEL_FLASH = 'deepseek-v4-flash'
MODEL_PRO = 'deepseek-v4-pro'
VALID_MODELS = (MODEL_FLASH, MODEL_PRO)

# reasoning_effort only accepts these two values on the DeepSeek API.
# low/medium silently collapse to high, xhigh collapses to max, so there's
# no point exposing anything but these two.
VALID_EFFORTS = ('high', 'max')

# Thinking mode is enabled by default on DeepSeek's API unless you
# explicitly send {"thinking": {"type": "disabled"}} on every request.
# When thinking is on, the reasoning trace shares the same token budget
# as the visible reply (max_tokens covers both), so a small budget can let
# reasoning eat the whole thing and leave nothing for the actual message.
MAX_TOKENS_NORMAL = 1024
MAX_TOKENS_THINKING = 4096

# Per-1M-token pricing (USD), per DeepSeek's official pricing page.
# Pro costs roughly 3x flash on input and output — tracked separately so
# !aicost is accurate when you've been toggling !turbo.
PRICING = {
    MODEL_FLASH: {'cache_hit': 0.0028, 'cache_miss': 0.14, 'output': 0.28},
    MODEL_PRO:   {'cache_hit': 0.003625, 'cache_miss': 0.435, 'output': 0.87},
}

#the very much needed ai shit

# ==========================================
# HELPER FUNCTIONS
# ==========================================

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

# ==========================================
# UI COMPONENTS (MENUS & VIEWS)
# ==========================================

#dropdown for thinking mode — scoped to one channel, not global
class ThinkingSelect(discord.ui.Select):
    def __init__(self, cog, channel_id):
        self.cog = cog
        self.channel_id = channel_id
        current = cog.thinking_effort.get(channel_id, 'off') if channel_id in cog.thinking_channels else 'off'

        options = [
            discord.SelectOption(label='Off', value='off', emoji='⚡',
                                  description='Instant, in-character replies (default)',
                                  default=(current == 'off')),
            discord.SelectOption(label='High', value='high', emoji='🧠',
                                  description='Standard reasoning before replying',
                                  default=(current == 'high')),
            discord.SelectOption(label='Max', value='max', emoji='🔬',
                                  description='Deepest reasoning — slower & pricier',
                                  default=(current == 'max')),
        ]
        super().__init__(placeholder='Choose thinking mode...', options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if not self.cog.is_allowed(interaction.user):
            await interaction.response.send_message("You don't have access to this.", ephemeral=True)
            return

        choice = self.values[0]
        if choice == 'off':
            self.cog.thinking_channels.discard(self.channel_id)
            self.cog.thinking_effort.pop(self.channel_id, None)
            desc = 'Thinking mode is now **off** — fast, in-character replies.'
        else:
            self.cog.thinking_channels.add(self.channel_id)
            self.cog.thinking_effort[self.channel_id] = choice
            desc = f'Thinking mode is now **on**, effort set to **{choice}**.'

        embed = discord.Embed(title='🧠 Thinking Mode', description=desc, color=0x2b2d31)
        embed.set_footer(text=f'model: {self.cog.active_model} · this setting is per-channel')
        await interaction.response.edit_message(embed=embed, view=None)

class ThinkingView(discord.ui.View):
    def __init__(self, cog, channel_id):
        super().__init__(timeout=60)
        self.add_item(ThinkingSelect(cog, channel_id))

# ==========================================
# MAIN COG CLASS
# ==========================================
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
        self.total_cost = 0.0
        self.session_start = time.time()
        self.webhooks = {}
        self._webhook_avatar_persona = {}  # cache to skip redundant avatar edits
        self.active_model = MODEL_FLASH
        self.active_channels = set()

        # per-channel thinking mode — NOT global, so one channel toggling
        # it on doesn't silently change behavior everywhere else
        self.thinking_channels = set()   # channel_ids with thinking ON
        self.thinking_effort = {}        # channel_id -> 'high' | 'max'
        self.temperature = 0.85

    # ==========================================
    # CORE AI LOGIC & LISTENERS
    # ==========================================
    
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

    #this sends the message as the persona, with vision support (vision support is broken rn) 
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
            for attempt in range(2):
                try:
                    if avatar_bytes and self._webhook_avatar_persona.get(message.channel.id) != persona:
                        await wh.edit(avatar=avatar_bytes)
                        self._webhook_avatar_persona[message.channel.id] = persona
                    for chunk in chunks:
                        await wh.send(content=chunk, username=display_name, wait=True)
                    return  # all chunks sent successfully
                except discord.NotFound:
                    # webhook was deleted externally — purge caches and recreate once
                    self.webhooks.pop(message.channel.id, None)
                    self._webhook_avatar_persona.pop(message.channel.id, None)
                    wh = await self.get_webhook(message.channel)
                    if wh is None:
                        break  # still Forbidden, fall through to bot-reply fallback
            # webhooks completely unavailable — fall back to sending as the bot

        # fallback: send as the bot itself (no webhook, or webhook failed twice)
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

        # ensure commands always work
        # (Removed: bot processes commands automatically by default, calling it here caused double execution)

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
        model_to_use = self.active_model  # always MODEL_FLASH or MODEL_PRO, never swapped

        #VISION SUPPORT END

        # ----- thinking mode (per channel, correct DeepSeek params) -----
        # The "thinking" key must be sent EVERY request — the API defaults
        # to enabled, so omitting it when you want it off does nothing.
        thinking_on = cid in self.thinking_channels
        thinking_body = {"type": "enabled" if thinking_on else "disabled"}
        if thinking_on:
            thinking_body["reasoning_effort"] = self.thinking_effort.get(cid, "high")
        kwargs = {
            "model": model_to_use,
            "messages": api_msgs,
            "max_tokens": MAX_TOKENS_THINKING if thinking_on else MAX_TOKENS_NORMAL,
            "extra_body": {"thinking": thinking_body},
            "temperature": self.temperature
        }

        async with message.channel.typing():
            try:
                resp = await self.client.chat.completions.create(**kwargs)
            except Exception as e:
                await message.reply(f'api error: {e}')
                mem.pop()
                return

        reply = (resp.choices[0].message.content or '').strip()
        if not reply:
            # thinking mode burned the whole max_tokens budget on reasoning
            # and left nothing for the actual answer
            reply = "...lost the thread there, try again or drop to !think off/high"

        if resp.usage:
            self.input_tokens += resp.usage.prompt_tokens
            self.output_tokens += resp.usage.completion_tokens

            # cache_hit/cache_miss split isn't always present depending on
            # SDK parsing — fall back to treating it all as cache-miss
            # (worst case) rather than silently undercounting cost
            cache_hit = getattr(resp.usage, 'prompt_cache_hit_tokens', 0) or 0
            cache_miss = getattr(resp.usage, 'prompt_cache_miss_tokens', 0) or 0
            if cache_hit == 0 and cache_miss == 0:
                cache_miss = resp.usage.prompt_tokens

            price = PRICING.get(model_to_use, PRICING[MODEL_FLASH])
            self.total_cost += (
                cache_hit * (price['cache_hit'] / 1_000_000)
                + cache_miss * (price['cache_miss'] / 1_000_000)
                + resp.usage.completion_tokens * (price['output'] / 1_000_000)
            )

        # NOTE: we only ever store `content` in memory, never
        # `reasoning_content`. Per DeepSeek's docs this is correct as long
        # as no tool calls are involved (they aren't here) — the CoT from
        # previous turns isn't supposed to be re-fed into context anyway.
        mem.append({'role': 'assistant', 'content': reply})

        # simple typing delay calculation for better rp
        words = len(reply.split())
        delay = max(1.5, min(words / 60 * 2, 5.0))
        await asyncio.sleep(delay)

        await self.send_as_char(message, reply, persona)

    # ==========================================
    # COMMANDS
    # ==========================================

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
        for t in ('flash', 'pro'):
            self.prompts.pop((ctx.channel.id, t), None)
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

        embed = discord.Embed(title='ai usage', color=0x00ff00)
        embed.add_field(name='uptime', value=f'{hours}h {mins}m', inline=False)
        embed.add_field(name='tokens', value=f'in: {self.input_tokens:,}\nout: {self.output_tokens:,}', inline=True)
        embed.add_field(name='cost', value=f'~${self.total_cost:.5f}', inline=True)
        embed.set_footer(text='cost tracked per-call using actual model + cache pricing')
        await ctx.reply(embed=embed)

    #THIS IS WHERE TURBO COMMANDS LIVE IN
    @commands.command()
    async def turbo(self, ctx):
        if not self.is_allowed(ctx.author):
            return
        if self.active_model == MODEL_FLASH:
            self.active_model = MODEL_PRO
            self.prompts.clear()
            await ctx.reply(f'switched to **{MODEL_PRO}**')
        else:
            self.active_model = MODEL_FLASH
            self.prompts.clear()
            await ctx.reply(f'switched to **{MODEL_FLASH}**')

    #this shows the current model, only usable by me and hizu
    @commands.command()
    async def current(self, ctx):
        if not self.is_allowed(ctx.author):
            return
        await ctx.reply(f'currently using: **{self.active_model}**')

    #this opens the thinking mode menu (off/high/max), only usable by me and hizu
    @commands.command()
    async def think(self, ctx):
        if not self.is_allowed(ctx.author):
            return
        cid = ctx.channel.id
        state = 'on' if cid in self.thinking_channels else 'off'
        effort = self.thinking_effort.get(cid)

        embed = discord.Embed(title='🧠 Thinking Mode', color=0x2b2d31)
        embed.add_field(name='current state', value=f'**{state}**' + (f' (`{effort}`)' if effort else ''), inline=False)
        embed.set_footer(text=f'model: {self.active_model} · this channel only')
        await ctx.reply(embed=embed, view=ThinkingView(self, cid))

    #this checks thinking status without opening the menu, only usable by me and hizu
    @commands.command()
    async def thinkstatus(self, ctx):
        if not self.is_allowed(ctx.author):
            return
        cid = ctx.channel.id
        if cid in self.thinking_channels:
            effort = self.thinking_effort.get(cid, 'high')
            await ctx.reply(f"🧠 thinking is **on** for this channel (effort: `{effort}`, model: `{self.active_model}`)")
        else:
            await ctx.reply(f"🧠 thinking is **off** for this channel (model: `{self.active_model}`)")

    #this is for bot temperature management
    @commands.command()
    async def temperature(self, ctx, value: float):
        if not self.is_allowed(ctx.author):
            return
        if not (0.0 <= value <= 2.0):
            return await ctx.reply("temperature must be between 0.0 and 2.0.")
            
        self.temperature = value
        await ctx.reply(f"🌡️ **Temperature set to {value}**\n*(Lower = stricter/robotic, Higher = creative/chaotic)*")

async def setup(bot):
    await bot.add_cog(AIRoleplay(bot))
