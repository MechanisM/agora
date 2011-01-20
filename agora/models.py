import datetime

from django.core.urlresolvers import reverse
from django.db import models
from django.db.models.signals import post_save, post_delete

from django.contrib.auth.models import User

from agora.managers import ForumThreadManager


# this is the glue to the activity events framework, provided as a no-op here
def issue_update(kind, **kwargs):
    pass


class ForumCategory(models.Model):
    
    title = models.CharField(max_length=100)
    
    parent = models.ForeignKey("self", null=True, blank=True, related_name="subcategories")
    
    # @@@ total descendant forum count?
    # @@@ make group-aware
    
    class Meta:
        verbose_name_plural = "forum categories"
    
    def __unicode__(self):
        return self.title
    
    def get_absolute_url(self):
        return reverse("agora_category", args=(self.pk,))


class Forum(models.Model):
    
    title = models.CharField(max_length=100)
    description = models.TextField()
    
    # must only have one of these (or neither):
    parent = models.ForeignKey("self",
        null = True,
        blank = True,
        related_name = "subforums"
    )
    category = models.ForeignKey(ForumCategory,
        null = True,
        blank = True,
        related_name = "forums"
    )
    
    # @@@ make group-aware
    
    last_modified = models.DateTimeField(
        default = datetime.datetime.now,
        editable = False
    )
    last_reply = models.ForeignKey("ForumReply", null=True, editable=False)
    
    view_count = models.IntegerField(default=0, editable=False)
    reply_count = models.IntegerField(default=0, editable=False)
    
    # this is what gets run normally
    def inc_views(self):
        self.view_count += 1
        self.save()
    
    # this can be used occasionally to get things back in sync
    def update_view_count(self):
        view_count = 0
        for thread in self.threads.all():
            view_count += thread.view_count
        self.view_count = view_count
        self.save()
    
    def update_reply_count(self):
        reply_count = 0
        for forum in self.subforums.all():
            forum.update_reply_count()
            reply_count += forum.reply_count
        for thread in self.threads.all():
            thread.update_reply_count()
            reply_count += thread.reply_count
        self.reply_count = reply_count
        self.save()
    
    def new_reply(self, reply):
        self.reply_count += 1 # if this gets out of sync run update_reply_count
        self.last_modified = reply.created
        self.last_reply = reply
        self.save()
        if self.parent:
            self.parent.new_reply(reply)
    
    def __unicode__(self):
        return self.title


class ForumPost(models.Model):
    
    author = models.ForeignKey(User, related_name="%(app_label)s_%(class)s_related")
    # @@@ support markup
    content = models.TextField()
    created = models.DateTimeField(default=datetime.datetime.now, editable=False)
    
    class Meta:
        abstract = True
    
    # allow editing for short period after posting
    def editable(self, user):
        if user == self.author:
            if datetime.datetime.now() < self.created + datetime.timedelta(minutes=30): # @@@ factor out time interval
                return True
        return False


class ForumThread(ForumPost):
    
    # used for code that needs to know the kind of post this object is.
    kind = "thread"
    
    forum = models.ForeignKey(Forum, related_name="threads")
    
    title = models.CharField(max_length=100)
    
    last_modified = models.DateTimeField(
        default = datetime.datetime.now,
        editable = False
    )
    last_reply = models.ForeignKey("ForumReply", null=True, editable=False) # only temporarily null
    
    # @@@ sticky threads
    # @@@ closed threads
    
    view_count = models.IntegerField(default=0, editable=False)
    reply_count = models.IntegerField(default=0, editable=False)
    subscriber_count = models.IntegerField(default=0, editable=False)
    
    objects = ForumThreadManager()
    
    def inc_views(self):
        self.view_count += 1
        self.save()
        self.forum.inc_views()
    
    def update_reply_count(self):
        self.reply_count = self.replies.all().count()
        self.save()
    
    def update_subscriber_count(self):
        self.subscriber_count = self.subscriptions.count()
        self.save()
    
    def new_reply(self, reply):
        self.reply_count += 1
        self.last_modified = reply.created
        self.last_reply = reply
        self.save()
        self.forum.new_reply(reply)
    
    def subscribe(self, user):
        """
        Subscribes the given user to this thread (handling duplicates)
        """
        ThreadSubscription.objects.get_or_create(thread=self, user=user)
    
    def unsubscribe(self, user):
        try:
            subscription = ThreadSubscription.objects.get(thread=self, user=user)
        except ThreadSubscription.DoesNotExist:
            return
        else:
            subscription.delete()
    
    def subscribed(self, user):
        if user.is_anonymous():
            return False
        try:
            ThreadSubscription.objects.get(thread=self, user=user)
        except ThreadSubscription.DoesNotExist:
            return False
        else:
            return True
    
    def __unicode__(self):
        return self.title


class ForumReply(ForumPost):
    
    # used for code that needs to know the kind of post this object is.
    kind = "reply"
    
    thread = models.ForeignKey(ForumThread, related_name="replies")
    
    class Meta:
        verbose_name = "forum reply"
        verbose_name_plural = "forum replies"


class UserPostCount(models.Model):
    
    user = models.ForeignKey(User, related_name="post_count")
    count = models.IntegerField(default=0)


class ThreadSubscription(models.Model):
    
    thread = models.ForeignKey(ForumThread, related_name="subscriptions")
    user = models.ForeignKey(User, related_name="forum_subscriptions")
    
    class Meta:
        unique_together = [("thread", "user")]


def signal(signals, sender=None):
    def _wrapped(func):
        if not hasattr(signals, "__iter__"):
            _s = [signals]
        else:
            _s = signals
        for s in _s:
            s.connect(func, sender=sender)
        return func
    return _wrapped


@signal(post_save, ForumReply)
def forum_reply_save(sender, instance=None, created=False, **kwargs):
    if instance and created:
        thread = instance.thread
        thread.new_reply(instance)
        
        # @@@ this next part could be manager method
        post_count, created = UserPostCount.objects.get_or_create(user=instance.author)
        post_count.count += 1
        post_count.save()


@signal([post_save, post_delete], ThreadSubscription)
def forum_subscription_update(sender, instance=None, created=False, **kwargs):
    if instance and created:
        thread = instance.thread
        thread.update_subscriber_count()


# @@@ handling deletion? (e.g. counts, last_modified, last_reply)
