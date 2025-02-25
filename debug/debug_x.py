import tweepy

TWITTER_API_KEY = "V6POxbYzuoAiFmSdpYDnVxyLn"
TWITTER_API_SECRET = "fIwtYAvIlnt58Ceri2gUxaXLy34sh3uXLQvpUfZHlrjOOzj480"
TWITTER_ACCESS_TOKEN = "457161032-AQFt3DYbzeVDsMJSyPUmRXQ3NxsfimLIwkrV0EZ6"
TWITTER_ACCESS_SECRET= "3scLGlfXNcwwuFKongUE4zVicmhmRtPaNHsbvp48LG1Ow"

auth = tweepy.OAuthHandler(TWITTER_API_KEY, TWITTER_API_SECRET)
auth.set_access_token(TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET)
api = tweepy.API(auth)

try:
    tweets = api.user_timeline(screen_name='@binance', count=10)
    print(tweets[0].text)
except Exception as e:
    print(f"Error: {e}")