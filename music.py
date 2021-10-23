"""
Github Source code Note:
The original owner of the base code is: https://github.com/vbe0201
I just changed the code like i want to or fixed bugs like loop or stop.
"""

import asyncio
import datetime
import functools
import itertools
import json
import math
import random
import traceback

import discord
import youtube_dl
from discord import Embed
from youtube_dl import YoutubeDL
from async_timeout import timeout
from discord.ext import commands

import Cogs._json
# Silence useless bug reports messages
from bot_config.settings import colour, botowner, avatarowner

youtube_dl.utils.bug_reports_message = lambda: ''

with open('./bot_config/blacklist.json', 'r') as file:
    blacklist = json.load(file)
    blacklistedUsers = blacklist["blacklistedUsers"]


class VoiceError(Exception):
    pass


class YTDLError(Exception):
    pass


class YTDLSource(discord.PCMVolumeTransformer):
    YTDL_OPTIONS = {
        'format': 'bestaudio/best',
        'extractaudio': True,
        'audioformat': 'mp3',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'auto',
        'source_address': '0.0.0.0',
    }

    FFMPEG_OPTIONS = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn',
    }

    ytdl = YoutubeDL(YTDL_OPTIONS)

    def __init__(self, ctx: commands.Context, source: discord.FFmpegPCMAudio, *, data: dict, volume: float = 0.5):
        super().__init__(source, volume)

        self.requester = ctx.author
        self.channel = ctx.channel
        self.data = data

        self.uploader = data.get('uploader')
        self.uploader_url = data.get('uploader_url')
        date = data.get('upload_date')
        self.upload_date = date[6:8] + '.' + date[4:6] + '.' + date[0:4]
        self.title = data.get('title')
        self.thumbnail = data.get('thumbnail')
        self.description = data.get('description')
        self.duration = self.parse_duration(int(data.get('duration')))
      #  self.current = self.parse_duration(int(data.get("progression")))
        self.tags = data.get('tags')
        self.url = data.get('webpage_url')
        self.views = data.get('view_count')
        self.likes = data.get('like_count')
        self.dislikes = data.get('dislike_count')
        self.stream_url = data.get('url')


    def __str__(self):
        return '**{0.title}** by **{0.uploader}**'.format(self)

    @classmethod
    async def create_source(cls, ctx: commands.Context, search, *, loop: asyncio.BaseEventLoop = None):
        loop = loop or asyncio.get_event_loop()

        partial = functools.partial(cls.ytdl.extract_info, search, download=False, process=False)
        data = await loop.run_in_executor(None, partial)

        if data is None:
            raise YTDLError('Couldn\'t find anything that matches `{}`'.format(search))

        if 'entries' not in data:
            process_info = data
        else:
            process_info = None
            for entry in data['entries']:
                if entry:
                    process_info = entry
                    break

            if process_info is None:
                raise YTDLError('Couldn\'t find anything that matches `{}`'.format(search))

        webpage_url = process_info['webpage_url']
        partial = functools.partial(cls.ytdl.extract_info, webpage_url, download=False)
        processed_info = await loop.run_in_executor(None, partial)

        if processed_info is None:
            raise YTDLError('Couldn\'t fetch `{}`'.format(webpage_url))

        if 'entries' not in processed_info:
            info = processed_info
        else:
            info = None
            while info is None:
                try:
                    info = processed_info['entries'].pop(0)
                except IndexError:
                    raise YTDLError('Couldn\'t retrieve any matches for `{}`'.format(webpage_url))

        return cls(ctx, discord.FFmpegPCMAudio(info['url'], **cls.FFMPEG_OPTIONS), data=info)

    @staticmethod
    def parse_duration(duration: int):
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        duration = []
        if days > 0:
            duration.append('{} days'.format(days))
        if hours > 0:
            duration.append('{} hours'.format(hours))
        if minutes > 0:
            duration.append('{} minutes'.format(minutes))
        if seconds > 0:
            duration.append('{} seconds'.format(seconds))

        return ', '.join(duration)


class Song:
    __slots__ = ('source', 'requester')

    def __init__(self, source: YTDLSource):
        self.source = source
        self.requester = source.requester

    def create_embed(self):
        embed = (discord.Embed(title='▶️ __{0.source.title}__'.format(self),
                               description='Your song is **playing now.**'.format(self),
                               color=colour, url="{0.source.url}".format(self), timestamp=datetime.datetime.utcnow())
                 .add_field(name='⏰ Duration', value=self.source.duration)
                 .add_field(name='<:YouTube:896131889876840498> Uploader',
                            value='[{0.source.uploader}]({0.source.uploader_url})'.format(self))
                 .add_field(name='<:link:867720150572597258> URL', value='[Click here]({0.source.url})'.format(self))
              #  .add_field(name='progression', value={self.source.current})
                 .add_field(name="👀 Views", value=f"{self.source.views:,}")
                 .add_field(name="👍 Likes", value=f"{self.source.likes:,}")
                 .add_field(name="👎 Dislikes", value=f"{self.source.dislikes:,}")
                 .set_footer(text=f"Requested by {self.requester.display_name}", icon_url=self.requester.avatar_url)
                 .set_thumbnail(url=self.source.thumbnail))

        return embed


class SongQueue(asyncio.Queue):
    def __getitem__(self, item):
        if isinstance(item, slice):
            return list(itertools.islice(self._queue, item.start, item.stop, item.step))
        else:
            return self._queue[item]

    def __iter__(self):
        return self._queue.__iter__()

    def __len__(self):
        return self.qsize()

    def clear(self):
        self._queue.clear()

    def shuffle(self):
        random.shuffle(self._queue)

    def remove(self, index: int):
        del self._queue[index]


class VoiceState:
    def __init__(self, bot: commands.Bot, ctx: commands.Context):
        self.bot = bot
        self._ctx = ctx

        self.current = None
        self.voice = None
        self.next = asyncio.Event()
        self.songs = SongQueue()

        self.exists = True

        self._loop = False
        self._volume = 0.5
        self.skip_votes = set()

        self.audio_player = bot.loop.create_task(self.audio_player_task())

    def __del__(self):
        self.audio_player.cancel()

    @property
    def loop(self):
        return self._loop

    @loop.setter
    def loop(self, value: bool):
        self._loop = value

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = value

    @property
    def is_playing(self):
        return self.voice and self.current

    async def audio_player_task(self):
        while True:
            self.next.clear()
            self.now = None

            if self.loop == False:
                # Try to get the next song within 3 minutes.
                # If no song will be added to the queue in time,
                # the player will disconnect due to performance
                # reasons.
                try:
                    async with timeout(180):  # 3 minutes
                        self.current = await self.songs.get()
                except asyncio.TimeoutError:
                    self.bot.loop.create_task(self.stop())
                    self.exists = False
                    return

                self.current.source.volume = self._volume
                self.voice.play(self.current.source, after=self.play_next_song)
                await self.current.source.channel.send(embed=self.current.create_embed())

            # If the song is looped
            elif self.loop == True:
                self.now = discord.FFmpegPCMAudio(self.current.source.stream_url, **YTDLSource.FFMPEG_OPTIONS)
                self.voice.play(self.now, after=self.play_next_song)

            await self.next.wait()

    def play_next_song(self, error=None):
        if error:
            raise VoiceError(str(error))

        self.next.set()

    def skip(self):
        self.skip_votes.clear()

        if self.is_playing:
            self.voice.stop()

    async def stop(self):
        self.songs.clear()

        if self.voice:
            await self.voice.disconnect()
            self.voice = None


class Music(commands.Cog, name="Music"):
    """Music commands. **BETA Phase**"""


    def __init__(self, bot: commands.Bot):

        self.bot = bot
        self.voice_states = {}
        self._volume = 0.5

    @commands.Cog.listener()
    async def on_ready(self):
        print("Music Cog loaded.")

    def get_voice_state(self, ctx: commands.Context):
        state = self.voice_states.get(ctx.guild.id)
        if not state or not state.exists:
            state = VoiceState(self.bot, ctx)
            self.voice_states[ctx.guild.id] = state

        return state

    def cog_unload(self):
        for state in self.voice_states.values():
            self.bot.loop.create_task(state.stop())

    def cog_check(self, ctx: commands.Context):
        if not ctx.guild:
            raise commands.NoPrivateMessage('This command can\'t be used in DM channels.')

        return True

    async def __before_invoke(self, ctx):
        ctx.state = self.get_voice_state(ctx)

    async def cog_before_invoke(self, ctx: commands.Context):
        ctx.voice_state = self.get_voice_state(ctx)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        print(traceback.print_exception(type(error), error, error.__traceback__))

    @commands.command(name='join')
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def _join(self, ctx: commands.Context, *, channel: discord.VoiceChannel = None):
        """Joins your Voice Channel or a mentioned one.
        """

        if not channel and not ctx.author.voice:
            raise VoiceError('You are not connected to a voice channel, or no voice channel was specified.')

        destination = channel or ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            await ctx.reply(f"Moved to channel {destination.mention}.", mention_author=False)
            return

        ctx.voice_state.voice = await destination.connect()
        await ctx.reply(f"Connected to channel {destination.mention}", mention_author=False)

    @commands.command(name='leave', aliases=['disconnect'])
    @commands.has_guild_permissions(deafen_members=True, move_members=True)
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def _leave(self, ctx: commands.Context):

        if not ctx.voice_state.voice:
            return await ctx.reply('Not connected to any voice channel.')

        await ctx.voice_state.stop()
        await ctx.reply(f"Left {ctx.author.voice.channel.mention}", mention_author=False)
        del self.voice_states[ctx.guild.id]

    @commands.command(name='volume')
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def _volume(self, ctx: commands.Context, *, volume: float):
        """Sets the volume of the player."""

        if not ctx.voice_state.is_playing:
            return await ctx.reply('Nothing being played at the moment.')

        if 0 > volume > 100:
            return await ctx.reply('Volume must be __between__ `0` and `100`')
        if ctx.voice_state.loop:
            await ctx.reply("During a loop, changing volume does not work.")
            return
        elif ctx.voice_client.source:
            ctx.voice_state.current.source.volume = volume / 100
            embed = Embed(title=f':loud_sound: Volume set to {round(volume, 2)}%.',
                          description=f'{ctx.author.display_name} set the volume to '
                                      f'{round(volume, 2)}%.\n'
                                      f'Info: Once a loop was toggled this does not work.\n'
                                      'The bot would need to reconnect the voice channel.', colour=colour)

            await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name='now', aliases=['current', 'playing'])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def _now(self, ctx: commands.Context):
        """Displays the currently playing song."""

        await ctx.reply(embed=ctx.voice_state.current.create_embed(), mention_author=False)
        await ctx.reply(f"{self.voice_state.current}")

    @commands.command(name='pause')
    @commands.has_guild_permissions(deafen_members=True, move_members=True)
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def _pause(self, ctx: commands.Context):
        """Pauses the currently playing song."""

        if ctx.voice_state.is_playing and ctx.voice_state.voice.is_playing():
            ctx.voice_state.voice.pause()
            embed = discord.Embed(title=":pause_button: The song was paused.",
                                  description=f"{ctx.author.mention} paused the song.",
                                  colour=colour, timestamp=ctx.message.created_at)

            await ctx.reply(embed=embed, mention_author=False)
            await ctx.message.add_reaction('⏯')

    @commands.command(name='resume')
    @commands.has_guild_permissions(deafen_members=True, move_members=True)
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def _resume(self, ctx: commands.Context):
        """Resumes a currently paused song."""

        if ctx.voice_state.is_playing and ctx.voice_state.voice.is_paused():
            ctx.voice_state.voice.resume()
            embed = discord.Embed(title=":arrow_forward: The song is continuing.",
                                  description=f"{ctx.author.mention} continued the song",
                                  colour=colour, timestamp=ctx.message.created_at)
            embed.set_footer(text="Requested by " + ctx.author.display_name, icon_url=ctx.author.avatar_url)

            await ctx.reply(embed=embed, mention_author=False)
            await ctx.message.add_reaction('⏯')

    @commands.command(name='stop')
    @commands.has_guild_permissions(deafen_members=True, move_members=True)
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def _stop(self, ctx: commands.Context):
        """Stops playing song and clears the queue."""

        ctx.voice_state.songs.clear()
        if ctx.voice_state.is_playing:
            ctx.voice_state.voice.stop()
            embed = discord.Embed(title="⏹ Stopped the player.",
                                  description=f"{ctx.author.mention} stopped the player and cleared the queue.",
                                  colour=colour, timestamp=ctx.message.created_at)
            await ctx.message.add_reaction('⏹')
            await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name='skip')
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def _skip(self, ctx: commands.Context):
        """Vote to skip a song. The requester can automatically skip.
        3 skip votes are needed for the song to be skipped.
        """

        if not ctx.voice_state.is_playing:
            return await ctx.reply('Not playing any music right now.')

        voter = ctx.message.author
        if voter == ctx.voice_state.current.requester:
            await ctx.reply(f"{ctx.author.mention} skipped the song.")
            await ctx.message.add_reaction('⏭')
            ctx.voice_state.skip()

        elif voter.id not in ctx.voice_state.skip_votes:
            ctx.voice_state.skip_votes.add(voter.id)
            total_votes = len(ctx.voice_state.skip_votes)

            if total_votes >= 3:
                await ctx.message.add_reaction('⏭')
                ctx.voice_state.skip()
            else:
                await ctx.send('Skip vote added, currently at **{}/3**'.format(total_votes))
        else:
            await ctx.reply('You have already voted to skip this song.')

    @commands.command(name='queue')
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def _queue(self, ctx: commands.Context, *, page: int = 1):
        """Shows the player's queue."""

        if len(ctx.voice_state.songs) == 0:
            return await ctx.reply('Empty queue.')

        items_per_page = 10
        pages = math.ceil(len(ctx.voice_state.songs) / items_per_page)

        start = (page - 1) * items_per_page
        end = start + items_per_page

        queue = ''
        for i, song in enumerate(ctx.voice_state.songs[start:end], start=start):
            queue += '`{0}.` [**{1.source.title}**]({1.source.url})\n'.format(i + 1, song)

        embed = (discord.Embed(description='**{} tracks:**\n\n{}'.format(len(ctx.voice_state.songs), queue))
                 .set_footer(text='Viewing page {}/{}'.format(page, pages)))
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name='shuffle', disabled=True)
    @commands.is_owner()
    @commands.has_guild_permissions(deafen_members=True, move_members=True)
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def _shuffle(self, ctx: commands.Context):
        """Shuffles the queue."""

        if len(ctx.voice_state.songs) == 0:
            return await ctx.reply('Empty queue.')

        ctx.voice_state.songs.shuffle()
        await ctx.reply(f"Shuffle activated by {ctx.author.mention}.", mention_author=False)
        await ctx.message.add_reaction('✅')

    @commands.command(name='remove')
    @commands.has_guild_permissions(deafen_members=True, move_members=True)
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def _remove(self, ctx: commands.Context, index: int):
        """Removes a song from the queue. (Example: `<prefix>remove 3`)"""

        if len(ctx.voice_state.songs) == 0:
            return await ctx.reply('Empty queue.')

        ctx.voice_state.songs.remove(index - 1)
        await ctx.reply(f"{ctx.author.mention} removed song number **{index}** from the queue.")
        await ctx.message.add_reaction('✅')

    @commands.command(name='loop')
    @commands.is_owner()
    @commands.has_guild_permissions(deafen_members=True, move_members=True)
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def _loop(self, ctx: commands.Context):
        """Loop/unloop a song."""

        ctx.voice_state.loop = not ctx.voice_state.loop
        await ctx.reply("Toggled loop.", mention_author=False)

    @commands.command(name='play')
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def _play(self, ctx: commands.Context, *, search):
        """Plays a song.
        """

        if not ctx.voice_state.voice:
            await ctx.invoke(self._join)

        async with ctx.typing():
            try:
                source = await YTDLSource.create_source(ctx, search, loop=self.bot.loop)
            except YTDLError as e:
                await ctx.reply('An error occurred while processing this request: {}'.format(str(e)))
            else:
                song = Song(source)

                await ctx.voice_state.songs.put(song)
                embed = discord.Embed(title=f"<:plus_sign:896133590440620052>  __{str(source.title)}__",
                                      description="Added your song **into the queue.** \n"
                                                  f"Duration: *{source.duration}*",
                                      colour=colour, timestamp=ctx.message.created_at,
                                      url=str(source.url))
                embed.set_footer(text="Requested by " + ctx.author.display_name, icon_url=ctx.author.avatar_url)
                embed.set_thumbnail(url=str(source.thumbnail))
                await ctx.reply(embed=embed, mention_author=False)

    @_join.before_invoke
    @_play.before_invoke
    async def ensure_voice_state(self, ctx: commands.Context):
        if not ctx.author.voice or not ctx.author.voice.channel:
            raise commands.CommandError('You are not connected to any voice channel.')

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                raise commands.CommandError('Bot is already in a voice channel.')


def setup(bot):
    bot.add_cog(Music(bot))
