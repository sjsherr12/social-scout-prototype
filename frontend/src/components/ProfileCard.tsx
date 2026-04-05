import { useState } from "react";
import type { Profile } from "../lib/api";
import {
  User,
  Users,
  UserPlus,
  Heart,
  MessageCircle,
  TrendingUp,
  Calendar,
  Globe,
  Sparkles,
  MapPin,
} from "lucide-react";

function ratingColor(rating: number): string {
  if (rating >= 0.8) return "text-green-400";
  if (rating >= 0.6) return "text-yellow-400";
  if (rating >= 0.4) return "text-orange-400";
  return "text-red-400";
}

function StatBadge({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ size?: number; className?: string }>;
  label: string;
  value: string | number | null | undefined;
}) {
  if (value === null || value === undefined) return null;
  return (
    <div className="flex items-center gap-2 bg-zinc-800/50 rounded-lg px-3 py-2">
      <Icon size={14} className="text-zinc-500" />
      <span className="text-xs text-zinc-500">{label}</span>
      <span className="text-sm text-zinc-200 font-medium ml-auto">{value}</span>
    </div>
  );
}

export default function ProfileCard({ profile }: { profile: Profile }) {
  const [imgFailed, setImgFailed] = useState(false);

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6 max-w-2xl mx-auto">
      {/* Header */}
      <div className="flex items-start gap-4 mb-6">
        {profile.profile_image_url && !imgFailed ? (
          <img
            src={profile.profile_image_url}
            alt={profile.username}
            className="w-20 h-20 rounded-full object-cover border-2 border-zinc-700"
            onError={() => setImgFailed(true)}
          />
        ) : (
          <div className="w-20 h-20 rounded-full bg-zinc-800 flex items-center justify-center">
            <User size={32} className="text-zinc-600" />
          </div>
        )}
        <div className="flex-1 min-w-0">
          <h3 className="text-xl font-semibold text-white truncate">
            @{profile.username}
          </h3>
          {profile.name && (
            <p className="text-zinc-400 text-sm">{profile.name}</p>
          )}
          {profile.bio && (
            <p className="text-zinc-500 text-sm mt-1 line-clamp-2">
              {profile.bio}
            </p>
          )}
          {profile.profile_url && (
            <a
              href={profile.profile_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-400 text-xs hover:underline mt-1 inline-block"
            >
              View on Instagram
            </a>
          )}
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-4">
        <StatBadge icon={Users} label="Followers" value={profile.followers?.toLocaleString()} />
        <StatBadge icon={UserPlus} label="Following" value={profile.following?.toLocaleString()} />
        <StatBadge icon={Globe} label="Posts" value={profile.post_count?.toLocaleString()} />
        <StatBadge icon={Heart} label="Avg Likes" value={profile.avg_likes?.toLocaleString()} />
        <StatBadge icon={MessageCircle} label="Avg Comments" value={profile.avg_comments?.toLocaleString()} />
        <StatBadge
          icon={TrendingUp}
          label="Engagement"
          value={profile.engagement_rate !== null && profile.engagement_rate !== undefined ? `${profile.engagement_rate}%` : undefined}
        />
      </div>

      {/* Vision Data */}
      {(profile.estimated_age || profile.attractiveness_rating !== null) && (
        <div className="border-t border-zinc-800 pt-4 mt-4">
          <h4 className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-3">
            Vision Analysis
          </h4>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <StatBadge icon={Calendar} label="Age" value={profile.estimated_age ? `~${profile.estimated_age}` : undefined} />
            <StatBadge icon={User} label="Ethnicity" value={profile.ethnicity} />
            <StatBadge icon={User} label="Hair" value={profile.hair_color} />
            {profile.attractiveness_rating !== null && profile.attractiveness_rating !== undefined && (
              <div className="flex items-center gap-2 bg-zinc-800/50 rounded-lg px-3 py-2">
                <Sparkles size={14} className="text-zinc-500" />
                <span className="text-xs text-zinc-500">Rating</span>
                <span className={`text-sm font-bold ml-auto ${ratingColor(profile.attractiveness_rating)}`}>
                  {profile.attractiveness_rating.toFixed(2)}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Location */}
      {profile.location_slug && (
        <div className="border-t border-zinc-800 pt-4 mt-4">
          <div className="flex items-center gap-2 text-sm text-zinc-400">
            <MapPin size={14} />
            <span>{profile.location_slug}</span>
          </div>
        </div>
      )}
    </div>
  );
}
