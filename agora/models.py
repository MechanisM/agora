from datetime import datetime, timedelta

from django.core.urlresolvers import reverse
from django.db import models
from django.db.models.signals import post_save


# this is the glue to the activity events framework, provided as a no-op here
def issue_update(kind, **kwargs):
    pass


class ForumCategory(models.Model):
    
    name = models.CharField(max_length=100)
    parent = models.ForeignKey("self", null=True, blank=True, related_name="subcategories")
    # @@@ total descendant forum count?
    # @@@ make group-aware
    
    class Meta:
        verbose_name_plural = "forum categories"
    
    def __unicode__(self):
        return self.name


class Forum(models.Model):
    
    title = models.CharField(max_length=100)
    description = models.TextField()
    creation_date = models.DateTimeField(default=datetime.now)
    
    # must only have one of these (or neither):
    parent = models.ForeignKey("self", null=True, blank=True, related_name="subforums")
    category = models.ForeignKey(ForumCategory, null=True, blank=True, related_name="forums")
    
    # @@@ make group-aware
    
    last_modified = models.DateTimeField(default=datetime.now)
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
        self.last_modified = reply.timestamp
        self.last_reply = reply
        self.save()
        if self.parent:
            self.parent.new_reply(reply)
    
    def __unicode__(self):
        return self.title


class ForumThread(models.Model):
    
    forum = models.ForeignKey(Forum, related_name="threads")
    
    title = models.CharField(max_length=100)
    creation_date = models.DateTimeField(default=datetime.now)
    author = models.ForeignKey(User, related_name="threads_started")
    last_modified = models.DateTimeField(default=datetime.now)
    last_reply = models.ForeignKey("ForumReply", null=True) # only temporarily null
    
    view_count = models.IntegerField(default=0, editable=False)
    reply_count = models.IntegerField(default=0, editable=False)
    
    def inc_views(self):
        self.view_count += 1
        self.save()
        self.forum.inc_views()
    
    def update_reply_count(self):
        self.reply_count = self.replies.all().count()
        self.save()
    
    def new_reply(self, reply):
        self.reply_count += 1
        self.last_modified = reply.timestamp
        self.last_reply = reply
        self.save()
        self.forum.new_reply(reply)
    
    @property
    def first_post(self):
        return self.replies.get(is_first=True)
    
    def __unicode__(self):
        return self.title


class ForumPost(models.Model):
    
    thread = models.ForeignKey(ForumThread, related_name="replies")
    
    # @@@ TITLE?
    author = models.ForeignKey(User, related_name="replies")
    is_first = models.BooleanField(default=False)
    timestamp = models.DateTimeField(default=datetime.now)
    
    content = models.TextField()
    
    # allow editing for short period after posting
    def editable(self, user):
        if user == self.author:
            if datetime.now() < self.timestamp + timedelta(minutes=30): # @@@ factor out time interval
                return True
        return False


class UserPostCount(models.Model):
    
    user = models.ForeignKey(User, related_name="post_count")
    count = models.IntegerField(default=0)


def forum_thread_save(sender, instance=None, created=False, **kwargs):
    if instance and created:
        issue_update("forum_thread", user=instance.author, forum_thread=instance)


def forum_post_save(sender, instance=None, created=False, **kwargs):
    if instance and created:
        thread = instance.thread
        thread.new_reply(instance)
        # @@@ this next part could be manager method
        post_count, created = UserPostCount.objects.get_or_create(user=instance.author)
        post_count.count += 1
        post_count.save()
        # don't issue an update if this post is marked first
        if not instance.is_first:
            issue_update("forum_post", user=instance.author, forum_post=instance)


post_save.connect(forum_thread_save, sender=ForumThread)
post_save.connect(forum_post_save, sender=ForumPost)
